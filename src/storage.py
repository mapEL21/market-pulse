"""Сохранение прогонов в SQLite. Схема — раздел 9 spec.md.

SQL пишется руками, без ORM: объём данных небольшой, запросы простые,
а разбираться в SQL — часть цели проекта.

Соединение передаётся аргументом, а не создаётся внутри функций. Тогда тесты
работают с базой в памяти, а вызывающий код сам решает, когда открыть и когда
закрыть файл.
"""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.config import Config
from src.metrics import Metrics
from src.scoring import Observation

DB_PATH = Path("data") / "market_pulse.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY,
    started_at        TEXT NOT NULL,
    exchange          TEXT NOT NULL,
    source            TEXT NOT NULL,
    candle_time       TEXT NOT NULL,
    total_symbols     INTEGER,
    passed_filters    INTEGER,
    analysed_symbols  INTEGER,
    candidates_count  INTEGER,
    config_json       TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    exchange    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    rvol        REAL,
    rtc         REAL,
    ve          REAL,
    score       REAL NOT NULL,
    triggered   INTEGER NOT NULL,
    rank        INTEGER,
    price       REAL,
    change_pct  REAL,
    volume_usdt REAL,
    volume_median REAL,
    trades      INTEGER,
    trades_median REAL,
    range_pct   REAL,
    range_pct_median REAL,
    quote_volume_24h REAL
);

CREATE INDEX IF NOT EXISTS observations_by_run ON observations(run_id);

CREATE TABLE IF NOT EXISTS outcomes (
    observation_id INTEGER PRIMARY KEY REFERENCES observations(id),
    ret_30m     REAL,
    ret_2h      REAL,
    ret_8h      REAL,
    max_move_2h REAL,
    filled_at   TEXT
);
"""


def to_iso(moment_ms: int) -> str:
    """Миллисекунды UTC -> '2026-08-12 23:00:00'.

    В SQLite нет типа даты, время хранится текстом. Формат ISO 8601 выбран
    потому, что такие строки сравниваются и сортируются как строки — то есть
    BETWEEN и ORDER BY по ним работают правильно без преобразований.
    """
    moment = datetime.fromtimestamp(moment_ms / 1000, tz=timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def from_iso(text: str) -> int:
    """'2026-08-12 23:00:00' -> миллисекунды UTC. Обратна to_iso."""
    moment = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Открыть базу, создать схему, включить проверку внешних ключей.

    PRAGMA foreign_keys включается обязательно: SQLite по умолчанию внешние
    ключи не проверяет, и объявление REFERENCES остаётся просто комментарием.
    Прагма действует на соединение, а не на файл, поэтому её надо выполнять
    при каждом открытии.
    """
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    return connection


def run_exists(
    connection: sqlite3.Connection, exchange: str, candle_time: str
) -> bool:
    """Есть ли уже прогон этой биржи для этой свечи.

    Биржа входит в проверку: один запуск программы записывает по строке
    на каждую биржу, и это не дубли.

    Повторный прогон не запрещается: на этапе 10 понадобится пересчитать ту же
    свечу с другими порогами и сравнить. Но знать о дубле полезно — при
    разработке программа запускается по несколько раз внутри одной свечи.
    """
    row = connection.execute(
        "SELECT 1 FROM runs WHERE exchange = ? AND candle_time = ? LIMIT 1",
        (exchange, candle_time),
    ).fetchone()
    return row is not None


def save_run(
    connection: sqlite3.Connection,
    exchange: str,
    candle_open_ms: int,
    total_symbols: int,
    passed_filters: int,
    analysed_symbols: int,
    candidates_count: int,
    config: Config,
    source: str = "live",
) -> int:
    """Записать прогон одной биржи и вернуть его id.

    Строка на биржу, а не на запуск: воронка у каждой биржи своя, и в одну
    строку две не помещаются. Связь «оба списка получены одновременно»
    восстанавливается по candle_time.

    source различает живой прогон и исторический бэктест. Смешивать их
    в одной выборке нельзя: в бэктесте оборот за 24 ч считается по свечам,
    а в живом режиме берётся из тикера, и это разные числа.
    """
    cursor = connection.execute(
        """
        INSERT INTO runs (
            started_at, exchange, source, candle_time, total_symbols,
            passed_filters, analysed_symbols, candidates_count, config_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            to_iso(int(datetime.now(tz=timezone.utc).timestamp() * 1000)),
            exchange,
            source,
            to_iso(candle_open_ms),
            total_symbols,
            passed_filters,
            analysed_symbols,
            candidates_count,
            json.dumps(asdict(config), ensure_ascii=False),
        ),
    )
    connection.commit()
    return cursor.lastrowid


def latest_run(connection: sqlite3.Connection, exchange: str) -> tuple | None:
    """Последний прогон биржи: (id, source, candle_time, воронка).

    Сортировка по времени свечи, а не по id: прогоны бэктеста пишутся позже,
    но относятся к прошлому.
    """
    return connection.execute(
        """
        SELECT id, source, candle_time, total_symbols, passed_filters,
               analysed_symbols, candidates_count
        FROM runs
        WHERE exchange = ?
        ORDER BY candle_time DESC, id DESC
        LIMIT 1
        """,
        (exchange,),
    ).fetchone()


def run_candidates(connection: sqlite3.Connection, run_id: int) -> list[tuple]:
    """Кандидаты прогона по возрастанию ранга, со всеми числами для отчёта."""
    return connection.execute(
        """
        SELECT symbol, rank, score, rvol, rtc, ve, price, change_pct,
               volume_usdt, volume_median, trades, trades_median,
               range_pct, range_pct_median, quote_volume_24h
        FROM observations
        WHERE run_id = ? AND rank IS NOT NULL
        ORDER BY rank
        """,
        (run_id,),
    ).fetchall()


def observation_ids(connection: sqlite3.Connection, run_id: int) -> dict[str, int]:
    """symbol -> id для наблюдений одного прогона.

    Нужен бэктесту: он пишет outcomes сразу после наблюдений, а id строк
    executemany не возвращает.
    """
    rows = connection.execute(
        "SELECT symbol, id FROM observations WHERE run_id = ?", (run_id,)
    ).fetchall()
    return {symbol: observation_id for symbol, observation_id in rows}


def save_observations(
    connection: sqlite3.Connection,
    run_id: int,
    exchange: str,
    observations: list[Observation],
    turnover: dict[str, float],
) -> None:
    """Записать все наблюдения прогона, а не только кандидатов.

    Инструменты ниже порога нужны как контрольная группа: без них нельзя
    ни сравнить кандидатов с остальными, ни проверить задним числом другой
    порог. Кандидат отличается тем, что у него заполнен rank.

    turnover — оборот за 24 ч по символам. В живом режиме он приходит
    из тикера, в бэктесте считается по свечам, поэтому передаётся отдельно.

    Медианы хранятся рядом с текущими значениями, хотя формально выводятся
    делением (volume_median = volume / rvol). Причина в разделе 8 spec.md:
    отчёту нужны абсолютные числа «обычно столько-то», а восстанавливать их
    делением — значит ломаться на нулевом rvol и заставлять читателя базы
    догадываться, откуда взялось число.
    """
    connection.executemany(
        """
        INSERT INTO observations (
            run_id, exchange, symbol, rvol, rtc, ve, score, triggered, rank,
            price, change_pct, volume_usdt, volume_median, trades,
            trades_median, range_pct, range_pct_median, quote_volume_24h
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            _observation_row(run_id, exchange, observation, turnover)
            for observation in observations
        ],
    )
    connection.commit()


def ripe_observations(
    connection: sqlite3.Connection, ripe_before: str
) -> list[tuple]:
    """Наблюдения, у которых ещё нет outcome, а время уже пришло.

    Возвращает кортежи (id, exchange, symbol, price, candle_time).

    Берутся все наблюдения, а не только кандидаты: контрольная группа нужна
    ровно затем, чтобы было с чем сравнивать.

    LEFT JOIN с проверкой на NULL — обычный способ спросить «чего нет
    во второй таблице». Сравнение времени работает как сравнение строк:
    формат ISO для того и выбран.
    """
    return connection.execute(
        """
        SELECT ob.id, ob.exchange, ob.symbol, ob.price, r.candle_time
        FROM observations ob
        JOIN runs r ON r.id = ob.run_id
        LEFT JOIN outcomes o ON o.observation_id = ob.id
        WHERE o.observation_id IS NULL
          AND r.candle_time <= ?
        ORDER BY r.candle_time, ob.exchange, ob.score DESC
        """,
        (ripe_before,),
    ).fetchall()


def save_outcomes(connection: sqlite3.Connection, records: list[tuple]) -> None:
    """Записать результаты пачкой.

    records — кортежи (observation_id, ret_30m, ret_2h, ret_8h, max_move_2h).

    Пачкой, а не по одному: в бэктесте строк десятки тысяч, и commit на
    каждую превращает запись в самую долгую часть прогона — каждый commit
    это сброс на диск.
    """
    filled_at = to_iso(int(datetime.now(tz=timezone.utc).timestamp() * 1000))
    connection.executemany(
        """
        INSERT INTO outcomes (
            observation_id, ret_30m, ret_2h, ret_8h, max_move_2h, filled_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [(*record, filled_at) for record in records],
    )
    connection.commit()


def save_outcome(
    connection: sqlite3.Connection,
    observation_id: int,
    ret_30m: float | None,
    ret_2h: float | None,
    ret_8h: float | None,
    max_move_2h: float | None,
) -> None:
    """Записать результат одного наблюдения."""
    save_outcomes(
        connection, [(observation_id, ret_30m, ret_2h, ret_8h, max_move_2h)]
    )


def _observation_row(
    run_id: int, exchange: str, observation: Observation, turnover: dict[str, float]
) -> tuple:
    """Одна строка таблицы observations."""
    metrics: Metrics = observation.metrics
    return (
        run_id,
        exchange,
        observation.symbol,
        metrics.rvol,
        metrics.rtc,
        metrics.ve,
        observation.score,
        observation.triggered,
        observation.rank,
        metrics.close,
        metrics.change_pct,
        metrics.volume,
        metrics.volume_median,
        metrics.trades,
        metrics.trades_median,
        metrics.range_pct,
        metrics.range_pct_median,
        turnover.get(observation.symbol),
    )
