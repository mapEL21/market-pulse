"""Тесты сохранения прогонов.

Все тесты работают с базой в памяти: файлы на диске не создаются и убирать
за собой не нужно.
"""

import json
import sqlite3

import pytest

from src.config import DEFAULT
from src.metrics import Metrics
from src.scoring import Candidate
from src.storage import (
    connect,
    run_exists,
    save_candidates,
    save_run,
    to_iso,
)

# 2026-08-12 23:00:00 UTC
CANDLE_MS = 1786575600000


@pytest.fixture
def connection():
    """Соединение с базой в памяти, закрывается после теста.

    Именно close(), а не `with connect(...)`: у sqlite3 контекстный менеджер
    управляет транзакцией — фиксирует или откатывает её, — но соединение
    не закрывает. Легко перепутать и оставить открытый файл.
    """
    conn = connect(":memory:")
    try:
        yield conn
    finally:
        conn.close()


def make_candidate(symbol: str = "APRUSDT", rank: int = 1) -> Candidate:
    metrics = Metrics(
        rvol=793.3,
        rtc=459.5,
        ve=12.5,
        change_pct=6.46,
        close=0.3712,
        volume=21_000_000.0,
        volume_median=26_500.0,
        trades=223_538,
        trades_median=486.0,
        range_pct=11.0,
        range_pct_median=0.9,
    )
    return Candidate(symbol=symbol, rank=rank, score=3.0, metrics=metrics)


def store_run(conn, candidates_count: int = 5) -> int:
    return save_run(
        conn,
        candle_open_ms=CANDLE_MS,
        total_symbols=527,
        passed_filters=39,
        analysed_symbols=39,
        candidates_count=candidates_count,
        config=DEFAULT,
    )


def test_iso_time_is_sortable_text():
    """В SQLite нет типа даты. ISO-строки сравниваются как строки, поэтому
    ORDER BY и BETWEEN по ним работают правильно."""
    assert to_iso(CANDLE_MS) == "2026-08-12 23:00:00"
    assert to_iso(CANDLE_MS) < to_iso(CANDLE_MS + 60_000)


def test_schema_is_created_and_connect_is_repeatable(tmp_path):
    """CREATE TABLE IF NOT EXISTS: второе открытие той же базы не падает
    и видит записанное первым."""
    path = tmp_path / "test.db"

    first = connect(path)
    try:
        store_run(first)
    finally:
        first.close()

    second = connect(path)
    try:
        count = second.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    finally:
        second.close()

    assert count == 1


def test_run_is_saved_and_read_back(connection):
    run_id = store_run(connection)

    row = connection.execute(
        "SELECT candle_time, total_symbols, passed_filters, analysed_symbols,"
        " candidates_count FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()

    assert row == ("2026-08-12 23:00:00", 527, 39, 39, 5)


def test_config_json_round_trips(connection):
    """Прогон должен хранить те пороги и веса, с которыми он выполнялся,
    иначе на этапе 10 нельзя будет сравнивать прогоны между собой."""
    run_id = store_run(connection)

    stored = connection.execute(
        "SELECT config_json FROM runs WHERE id = ?", (run_id,)
    ).fetchone()[0]
    restored = json.loads(stored)

    assert restored["min_quote_volume_24h"] == DEFAULT.min_quote_volume_24h
    assert restored["rvol_threshold"] == DEFAULT.rvol_threshold
    assert restored["rvol_weight"] == DEFAULT.rvol_weight
    assert restored["top_n"] == DEFAULT.top_n


def test_candidates_are_linked_to_their_run(connection):
    run_id = store_run(connection)

    save_candidates(
        connection,
        run_id,
        exchange="BINANCE",
        candidates=[make_candidate("APRUSDT", 1), make_candidate("BRUSDT", 2)],
        turnover={"APRUSDT": 851_000_000.0, "BRUSDT": 122_200_000.0},
    )

    rows = connection.execute(
        "SELECT symbol, rank, quote_volume_24h FROM candidates"
        " WHERE run_id = ? ORDER BY rank",
        (run_id,),
    ).fetchall()

    assert rows == [("APRUSDT", 1, 851_000_000.0), ("BRUSDT", 2, 122_200_000.0)]


def test_candidate_stores_the_close_price(connection):
    """Поле price — цена закрытия анализируемой свечи. Без неё этап 9
    не сможет посчитать, что было с ценой после попадания в список."""
    run_id = store_run(connection)

    save_candidates(
        connection, run_id, "BINANCE", [make_candidate()], {"APRUSDT": 1.0}
    )

    price = connection.execute("SELECT price FROM candidates").fetchone()[0]
    assert price == 0.3712


def test_foreign_key_is_enforced(connection):
    """Проверяет, что PRAGMA foreign_keys включена. Без неё SQLite примет
    кандидата со ссылкой на несуществующий прогон, и связь между таблицами
    окажется фикцией."""
    with pytest.raises(sqlite3.IntegrityError):
        save_candidates(
            connection, 999, "BINANCE", [make_candidate()], {"APRUSDT": 1.0}
        )


def test_repeated_run_for_the_same_candle_is_detectable(connection):
    assert not run_exists(connection, to_iso(CANDLE_MS))

    store_run(connection)

    assert run_exists(connection, to_iso(CANDLE_MS))
    assert not run_exists(connection, to_iso(CANDLE_MS + 15 * 60 * 1000))
