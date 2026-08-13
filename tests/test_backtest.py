"""Тесты бэктеста: нарезка окон и постраничная загрузка истории.

Главный тест здесь — test_window_never_contains_future_candles. Заглядывание
в будущее не падает и не видно по результатам: оно просто делает их лучше,
чем они есть.
"""

from src.backtest import (
    CANDLES_PER_DAY,
    FUTURE_CANDLES,
    fetch_history,
    future_at,
    history_span,
    run_times,
    trades_24h,
    turnover_24h,
    window_at,
)
from src.candles import INTERVAL_MS, WINDOW_CANDLES
from src.exchanges.base import Candle, Instrument
from src.storage import connect

T = 1786575600000  # 2026-08-12 23:00:00 UTC


def candle(open_time: int, volume: float = 1000.0, trades: int | None = 10) -> Candle:
    return Candle(
        open_time=open_time,
        high=101.0,
        low=99.0,
        close=100.0,
        quote_volume=volume,
        trades=trades,
    )


def history(first_open: int, count: int, **kwargs) -> list[Candle]:
    return [candle(first_open + step * INTERVAL_MS, **kwargs) for step in range(count)]


def test_run_times_go_from_old_to_new():
    moments = run_times(T, count=4)

    assert moments == [
        T - 3 * INTERVAL_MS,
        T - 2 * INTERVAL_MS,
        T - INTERVAL_MS,
        T,
    ]


def test_history_span_covers_baseline_and_outcome_horizon():
    moments = run_times(T, count=96)

    start, end = history_span(moments)

    assert start == moments[0] - (WINDOW_CANDLES - 1) * INTERVAL_MS
    assert end == T + FUTURE_CANDLES * INTERVAL_MS


def test_window_never_contains_future_candles():
    """Ключевой тест этапа. История содержит свечи и после T; в окне
    не должно оказаться ни одной из них."""
    full = history(T - 300 * INTERVAL_MS, 400)

    window = window_at(full, T)

    assert len(window) == WINDOW_CANDLES
    assert window[-1].open_time == T
    assert max(item.open_time for item in window) == T
    assert all(item.open_time <= T for item in window)


def test_window_is_empty_when_history_is_too_short():
    """Неполное окно дало бы медиану по меньшему числу свечей, и метрики
    разных прогонов стали бы несравнимы."""
    short = history(T - 10 * INTERVAL_MS, 11)

    assert window_at(short, T) == []


def test_window_is_empty_when_the_requested_candle_is_missing():
    """Пропуск в истории: без проверки окно закончилось бы более ранней
    свечой, и прогон молча посчитался бы не на той свече."""
    full = history(T - 300 * INTERVAL_MS, 400)
    without_t = [item for item in full if item.open_time != T]

    assert window_at(without_t, T) == []


def test_future_window_covers_exactly_eight_hours():
    full = history(T - 10 * INTERVAL_MS, 100)

    future = future_at(full, T)

    assert future[0].open_time == T
    assert future[-1].open_time == T + FUTURE_CANDLES * INTERVAL_MS
    assert len(future) == FUTURE_CANDLES + 1


def test_turnover_sums_exactly_one_day_of_candles():
    full = history(T - 300 * INTERVAL_MS, 400, volume=1000.0)

    assert turnover_24h(full, T) == CANDLES_PER_DAY * 1000.0


def test_turnover_ignores_candles_after_the_moment():
    """Свечи после T в оборот попасть не должны — это то же заглядывание
    в будущее, только через фильтр ликвидности."""
    before = history(T - (CANDLES_PER_DAY - 1) * INTERVAL_MS, CANDLES_PER_DAY, volume=1000.0)
    after = history(T + INTERVAL_MS, 50, volume=1_000_000.0)

    assert turnover_24h(before + after, T) == CANDLES_PER_DAY * 1000.0


def test_trades_are_none_when_the_exchange_does_not_publish_them():
    without_trades = history(T - 300 * INTERVAL_MS, 400, trades=None)

    assert trades_24h(without_trades, T) is None
    assert turnover_24h(without_trades, T) > 0


def test_trades_sum_one_day():
    full = history(T - 300 * INTERVAL_MS, 400, trades=10)

    assert trades_24h(full, T) == CANDLES_PER_DAY * 10


class PagingClient:
    """Клиент с маленькой страницей: проверяет постраничную загрузку."""

    NAME = "FAKE"
    MAX_CANDLES_PER_REQUEST = 50

    def __init__(self, candles: list[Candle]):
        self.candles = candles
        self.pages = 0

    def fetch_raw_candles(self, symbol, interval, limit, end_time=None):
        self.pages += 1
        available = [item for item in self.candles if item.open_time <= end_time]
        return available[-limit:]

    def raw_candles_oldest_first(self, raw):
        return raw

    def parse_candle(self, raw):
        return raw


def test_history_is_collected_page_by_page(monkeypatch):
    import src.backtest

    monkeypatch.setattr(src.backtest.time, "sleep", lambda _: None)
    full = history(T - 199 * INTERVAL_MS, 200)
    client = PagingClient(full)

    result = fetch_history(client, "TESTUSDT", full[0].open_time, T)

    assert client.pages > 1                       # одной страницы не хватило
    assert len(result) == 200                     # собрано всё
    assert result[0].open_time == full[0].open_time
    assert result[-1].open_time == T
    assert result == sorted(result, key=lambda item: item.open_time)


class WholeExchange:
    """Биржа из одного инструмента с заранее выдуманной историей.

    Базовые свечи ровные, а на моменты прогонов приходится всплеск —
    так получается предсказуемый кандидат.
    """

    NAME = "BINANCE"
    MARKET = "test"
    MAX_CANDLES_PER_REQUEST = 300

    def __init__(self, spike_moments: set[int], first_open: int, count: int):
        self.candles = [
            self._make(first_open + step * INTERVAL_MS, spike_moments)
            for step in range(count)
        ]

    @staticmethod
    def _make(open_time: int, spike_moments: set[int]) -> Candle:
        if open_time in spike_moments:
            return Candle(open_time, 110.0, 90.0, 100.0, 10_000_000.0, 1000)
        return Candle(open_time, 100.5, 99.5, 100.0, 1_000_000.0, 100)

    def get_instruments(self):
        return [
            Instrument(
                symbol="TESTUSDT",
                status="TRADING",
                quote_volume_24h=100_000_000.0,
                trades_24h=1_000_000,
                last_price=100.0,
                change_pct_24h=0.0,
            )
        ]

    def fetch_raw_candles(self, symbol, interval, limit, end_time=None):
        available = [item for item in self.candles if item.open_time <= end_time]
        return available[-limit:]

    def raw_candles_oldest_first(self, raw):
        return raw

    def parse_candle(self, raw):
        return raw


def test_backtest_writes_runs_candidates_and_outcomes(monkeypatch):
    import src.backtest

    monkeypatch.setattr(src.backtest.time, "sleep", lambda _: None)

    moments = run_times(T, count=2)
    start, end = history_span(moments)
    total = (end - start) // INTERVAL_MS + 1
    client = WholeExchange(set(moments), start, total)

    connection = connect(":memory:")
    try:
        stats = src.backtest.backtest_exchange(
            client, connection, moments, log=lambda _: None
        )
        runs = connection.execute(
            "SELECT source, candle_time, candidates_count FROM runs ORDER BY id"
        ).fetchall()
        candidates = connection.execute(
            "SELECT symbol, ROUND(rvol, 1), ROUND(ve, 1) FROM candidates"
        ).fetchall()
        outcomes = connection.execute(
            "SELECT COUNT(*) FROM outcomes"
        ).fetchone()[0]
    finally:
        connection.close()

    assert stats.runs == 2
    assert stats.candidates == 2
    # Прогоны бэктеста обязаны быть отличимы от живых: в них оборот за 24 ч
    # считается по свечам, а не берётся из тикера.
    assert [row[0] for row in runs] == ["backtest", "backtest"]
    assert [row[2] for row in runs] == [1, 1]
    # Всплеск в десять раз выше обычного объёма и в двадцать раз шире.
    assert candidates == [("TESTUSDT", 10.0, 20.0), ("TESTUSDT", 10.0, 20.0)]
    # Результаты заполняются сразу: будущее уже лежит в загруженной истории.
    assert outcomes == 2


def test_history_stops_when_the_exchange_runs_out_of_candles(monkeypatch):
    """Инструмент листнут позже начала запрошенного периода — цикл обязан
    закончиться, а не запрашивать всё более раннее время бесконечно."""
    import src.backtest

    monkeypatch.setattr(src.backtest.time, "sleep", lambda _: None)
    full = history(T - 20 * INTERVAL_MS, 21)
    client = PagingClient(full)

    result = fetch_history(client, "NEWUSDT", T - 500 * INTERVAL_MS, T)

    assert len(result) == 21
