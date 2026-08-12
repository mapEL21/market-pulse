"""Тесты расчёта outcomes. Раздел 9.1 spec.md.

Свечи синтетические, ответы считаются в уме.
"""

import pytest

from src import outcomes
from src.config import DEFAULT
from src.exchanges.base import Candle
from src.metrics import Metrics
from src.outcomes import (
    EIGHT_HOURS_MS,
    HALF_HOUR_MS,
    RIPE_AFTER_MS,
    TWO_HOURS_MS,
    compute_outcome,
)
from src.scoring import Candidate
from src.storage import connect, save_candidates, save_run

T = 1786575600000  # 2026-08-12 23:00:00 UTC
STEP_MS = 15 * 60 * 1000
PRICE = 100.0


def candle(offset_ms: int, close: float, high: float = None, low: float = None) -> Candle:
    return Candle(
        open_time=T + offset_ms,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        quote_volume=1.0,
        trades=None,
    )


def flat_future() -> list[Candle]:
    """33 свечи по цене 100 — от T до T+8h включительно."""
    return [candle(step * STEP_MS, PRICE) for step in range(33)]


def test_returns_are_measured_from_the_right_candles():
    """Смещение отсчитывается от времени открытия анализируемой свечи:
    ret_30m берёт свечу T+30m, а не соседние."""
    future = flat_future()
    future[2] = candle(HALF_HOUR_MS, 104.0)      # T+30m
    future[8] = candle(TWO_HOURS_MS, 95.0)       # T+2h
    future[32] = candle(EIGHT_HOURS_MS, 130.0)   # T+8h

    result = compute_outcome(PRICE, T, future)

    assert result.ret_30m == pytest.approx(4.0)
    assert result.ret_2h == pytest.approx(-5.0)
    assert result.ret_8h == pytest.approx(30.0)


def test_neighbouring_candles_are_not_used():
    """Свечи T+15m и T+45m не должны влиять на ret_30m."""
    future = flat_future()
    future[1] = candle(STEP_MS, 500.0)
    future[3] = candle(3 * STEP_MS, 500.0)

    assert compute_outcome(PRICE, T, future).ret_30m == pytest.approx(0.0)


def test_max_move_takes_the_high_when_price_went_up():
    future = flat_future()
    future[4] = candle(4 * STEP_MS, 101.0, high=108.0, low=99.0)

    result = compute_outcome(PRICE, T, future)

    assert result.max_move_2h == pytest.approx(8.0)


def test_max_move_takes_the_low_when_price_went_down():
    """Величина без знака: падение на 12 % даёт 12, а не -12."""
    future = flat_future()
    future[4] = candle(4 * STEP_MS, 99.0, high=101.0, low=88.0)

    result = compute_outcome(PRICE, T, future)

    assert result.max_move_2h == pytest.approx(12.0)


def test_max_move_takes_the_larger_of_the_two_sides():
    future = flat_future()
    future[2] = candle(2 * STEP_MS, 100.0, high=105.0, low=100.0)
    future[6] = candle(6 * STEP_MS, 100.0, high=100.0, low=91.0)

    result = compute_outcome(PRICE, T, future)

    assert result.max_move_2h == pytest.approx(9.0)


def test_max_move_ignores_the_analysed_candle_itself():
    """Движение самой анализируемой свечи уже учтено метрикой VE."""
    future = flat_future()
    future[0] = candle(0, PRICE, high=150.0, low=50.0)

    result = compute_outcome(PRICE, T, future)

    assert result.max_move_2h == pytest.approx(0.0)


def test_max_move_ignores_candles_beyond_two_hours():
    future = flat_future()
    future[9] = candle(9 * STEP_MS, 100.0, high=200.0, low=100.0)

    result = compute_outcome(PRICE, T, future)

    assert result.max_move_2h == pytest.approx(0.0)


def test_missing_candle_gives_none_not_zero():
    """Инструмент делистнули или в истории пропуск. Ноль означал бы
    «цена не изменилась» — утверждение, которого мы не делали."""
    only_first_hour = [candle(step * STEP_MS, PRICE) for step in range(5)]

    result = compute_outcome(PRICE, T, only_first_hour)

    assert result.ret_30m == pytest.approx(0.0)
    assert result.ret_2h is None
    assert result.ret_8h is None


def test_no_future_candles_at_all():
    result = compute_outcome(PRICE, T, [])

    assert result.ret_30m is None
    assert result.ret_2h is None
    assert result.ret_8h is None
    assert result.max_move_2h is None


# --- заполнение по базе, без сети ---


class FakeClient:
    """Клиент, отдающий заранее заданные свечи вместо запроса к бирже."""

    NAME = "BINANCE"

    def __init__(self, candles: list[Candle]):
        self.candles = candles
        self.calls = 0

    def fetch_raw_candles(self, symbol, interval, limit, end_time=None):
        self.calls += 1
        return self.candles

    def raw_candles_oldest_first(self, raw):
        return raw

    def parse_candle(self, raw):
        return raw


def store_candidate(connection, symbol: str = "APRUSDT", rank: int = 1) -> None:
    run_id = save_run(
        connection,
        exchange="BINANCE",
        candle_open_ms=T,
        total_symbols=527,
        passed_filters=39,
        analysed_symbols=39,
        candidates_count=1,
        config=DEFAULT,
    )
    metrics = Metrics(
        rvol=4.0, rtc=3.0, ve=2.5, change_pct=1.0, close=PRICE,
        volume=1.0, volume_median=1.0, trades=1, trades_median=1.0,
        range_pct=1.0, range_pct_median=1.0,
    )
    save_candidates(
        connection,
        run_id,
        "BINANCE",
        [Candidate(symbol=symbol, rank=rank, score=2.0, metrics=metrics)],
        {symbol: 1.0},
    )


@pytest.fixture
def connection(monkeypatch):
    conn = connect(":memory:")
    monkeypatch.setitem(outcomes.CLIENTS, "BINANCE", FakeClient(flat_future()))
    monkeypatch.setattr(outcomes.time, "sleep", lambda _: None)
    try:
        yield conn
    finally:
        conn.close()


def test_unripe_candidates_are_left_alone(connection):
    """Кандидат созревает только через 8 ч 15 мин: раньше нужной свечи
    просто не существует."""
    store_candidate(connection)

    stats = outcomes.fill_outcomes(connection, now_ms=T + 4 * 60 * 60 * 1000)

    assert stats.ripe == 0
    assert stats.filled == 0


def test_ripe_candidate_gets_an_outcome(connection):
    store_candidate(connection)

    stats = outcomes.fill_outcomes(connection, now_ms=T + RIPE_AFTER_MS)

    assert (stats.ripe, stats.filled, stats.no_data) == (1, 1, 0)
    row = connection.execute(
        "SELECT ret_30m, ret_2h, ret_8h, max_move_2h FROM outcomes"
    ).fetchone()
    assert row == (0.0, 0.0, 0.0, 0.0)


def test_already_filled_candidates_are_not_refetched(connection):
    store_candidate(connection)
    outcomes.fill_outcomes(connection, now_ms=T + RIPE_AFTER_MS)

    again = outcomes.fill_outcomes(connection, now_ms=T + RIPE_AFTER_MS)

    assert again.ripe == 0
    assert again.fetched == 0


def test_candles_are_fetched_once_per_instrument_and_candle(connection):
    """Один и тот же кандидат встречается в нескольких прогонах одной свечи —
    качать историю для него дважды незачем."""
    store_candidate(connection)
    store_candidate(connection)

    stats = outcomes.fill_outcomes(connection, now_ms=T + RIPE_AFTER_MS)

    assert stats.ripe == 2
    assert stats.filled == 2
    assert stats.fetched == 1


def test_missing_history_is_counted_not_written(connection, monkeypatch):
    """Строка из одних NULL означала бы «результат посчитан и пуст».
    Инструмент без данных лучше оставить незаполненным."""
    monkeypatch.setitem(outcomes.CLIENTS, "BINANCE", FakeClient([]))
    store_candidate(connection)

    stats = outcomes.fill_outcomes(connection, now_ms=T + RIPE_AFTER_MS)

    assert (stats.filled, stats.no_data) == (0, 1)
    assert connection.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 0
