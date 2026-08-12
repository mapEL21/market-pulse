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
from src.scoring import Candidate

DB_PATH = Path("data") / "market_pulse.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY,
    started_at        TEXT NOT NULL,
    exchange          TEXT NOT NULL,
    candle_time       TEXT NOT NULL,
    total_symbols     INTEGER,
    passed_filters    INTEGER,
    analysed_symbols  INTEGER,
    candidates_count  INTEGER,
    config_json       TEXT
);

CREATE TABLE IF NOT EXISTS candidates (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    exchange    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    score       REAL NOT NULL,
    rvol        REAL,
    rtc         REAL,
    ve          REAL,
    price       REAL,
    change_pct  REAL,
    volume_usdt REAL,
    trades      INTEGER,
    quote_volume_24h REAL
);

CREATE TABLE IF NOT EXISTS outcomes (
    candidate_id INTEGER PRIMARY KEY REFERENCES candidates(id),
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
) -> int:
    """Записать прогон одной биржи и вернуть его id.

    Строка на биржу, а не на запуск: воронка у каждой биржи своя, и в одну
    строку две не помещаются. Связь «оба списка получены одновременно»
    восстанавливается по candle_time.
    """
    cursor = connection.execute(
        """
        INSERT INTO runs (
            started_at, exchange, candle_time, total_symbols, passed_filters,
            analysed_symbols, candidates_count, config_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            to_iso(int(datetime.now(tz=timezone.utc).timestamp() * 1000)),
            exchange,
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


def save_candidates(
    connection: sqlite3.Connection,
    run_id: int,
    exchange: str,
    candidates: list[Candidate],
    turnover: dict[str, float],
) -> None:
    """Записать кандидатов одного прогона.

    turnover — оборот за 24 ч по символам. Он приходит из тикера, а не из
    метрик, поэтому передаётся отдельно.
    """
    connection.executemany(
        """
        INSERT INTO candidates (
            run_id, exchange, symbol, rank, score,
            rvol, rtc, ve, price, change_pct,
            volume_usdt, trades, quote_volume_24h
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            _candidate_row(run_id, exchange, candidate, turnover)
            for candidate in candidates
        ],
    )
    connection.commit()


def ripe_candidates(
    connection: sqlite3.Connection, ripe_before: str
) -> list[tuple]:
    """Кандидаты, у которых ещё нет outcome, а время уже пришло.

    Возвращает кортежи (id, exchange, symbol, price, candle_time).

    LEFT JOIN с проверкой на NULL — обычный способ спросить «чего нет
    во второй таблице». Сравнение времени работает как сравнение строк:
    формат ISO для того и выбран.
    """
    return connection.execute(
        """
        SELECT c.id, c.exchange, c.symbol, c.price, r.candle_time
        FROM candidates c
        JOIN runs r ON r.id = c.run_id
        LEFT JOIN outcomes o ON o.candidate_id = c.id
        WHERE o.candidate_id IS NULL
          AND r.candle_time <= ?
        ORDER BY r.candle_time, c.exchange, c.rank
        """,
        (ripe_before,),
    ).fetchall()


def save_outcome(
    connection: sqlite3.Connection,
    candidate_id: int,
    ret_30m: float | None,
    ret_2h: float | None,
    ret_8h: float | None,
    max_move_2h: float | None,
) -> None:
    """Записать результат кандидата."""
    connection.execute(
        """
        INSERT INTO outcomes (
            candidate_id, ret_30m, ret_2h, ret_8h, max_move_2h, filled_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_id,
            ret_30m,
            ret_2h,
            ret_8h,
            max_move_2h,
            to_iso(int(datetime.now(tz=timezone.utc).timestamp() * 1000)),
        ),
    )
    connection.commit()


def _candidate_row(
    run_id: int, exchange: str, candidate: Candidate, turnover: dict[str, float]
) -> tuple:
    """Одна строка таблицы candidates."""
    metrics: Metrics = candidate.metrics
    return (
        run_id,
        exchange,
        candidate.symbol,
        candidate.rank,
        candidate.score,
        metrics.rvol,
        metrics.rtc,
        metrics.ve,
        metrics.close,
        metrics.change_pct,
        metrics.volume,
        metrics.trades,
        turnover.get(candidate.symbol),
    )
