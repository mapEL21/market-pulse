"""Тесты расчёта метрик.

Окна собираются из чисел, подобранных так, чтобы ответ считался в уме.
Ни сети, ни кэша.
"""

import pytest

from src.candles import BASELINE_CANDLES, WINDOW_CANDLES
from src.exchanges.binance import Candle
from src.metrics import (
    compute_metrics,
    normalized_true_range,
    true_range,
)

HALF = BASELINE_CANDLES // 2  # 96


def build_window(
    volumes: list[float] | None = None,
    trades: list[int] | None = None,
    closes: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> list[Candle]:
    """Окно из WINDOW_CANDLES свечей. Не переданное заполняется константами."""
    size = WINDOW_CANDLES
    volumes = [1000.0] * size if volumes is None else volumes
    trades = [100] * size if trades is None else trades
    closes = [100.0] * size if closes is None else closes
    highs = [close + 1 for close in closes] if highs is None else highs
    lows = [close - 1 for close in closes] if lows is None else lows

    return [
        Candle(
            open_time=index,
            high=highs[index],
            low=lows[index],
            close=closes[index],
            quote_volume=volumes[index],
            trades=trades[index],
        )
        for index in range(size)
    ]


def test_window_of_wrong_length_is_rejected():
    with pytest.raises(ValueError):
        compute_metrics(build_window()[:-1])


def test_baseline_excludes_both_the_extra_candle_and_the_current_one():
    """Ключевой тест раскладки окна.

    Медиана базы должна получиться 15: половина свечей по 10, половина по 20.
    Свеча window[0] с оборотом 999 999 нужна только для первой TR, а текущая
    свеча не должна попадать в собственную базу — иначе сильный всплеск
    приподнимал бы медиану и сам себя занижал.
    """
    volumes = [999_999.0] + [10.0] * HALF + [20.0] * HALF + [4_000.0]

    result = compute_metrics(build_window(volumes=volumes))

    assert len(volumes) == WINDOW_CANDLES
    assert result.volume_median == 15.0
    assert result.volume == 4_000.0
    assert result.rvol == pytest.approx(4_000 / 15)


def test_rtc_is_trades_over_baseline_median():
    trades = [999_999] + [10] * HALF + [20] * HALF + [500]

    result = compute_metrics(build_window(trades=trades))

    assert result.trades_median == 15.0
    assert result.rtc == pytest.approx(500 / 15)


def test_true_range_for_a_candle_without_a_gap():
    """Прошлое закрытие внутри диапазона свечи — работает High - Low."""
    candle = Candle(open_time=0, high=105.0, low=95.0, close=100.0,
                    quote_volume=1.0, trades=1)

    assert true_range(candle, previous_close=100.0) == 10.0


def test_true_range_for_a_gap_up():
    """Свеча целиком выше прошлого закрытия: реальное движение больше,
    чем её собственный размах."""
    candle = Candle(open_time=0, high=120.0, low=115.0, close=118.0,
                    quote_volume=1.0, trades=1)

    # High - Low = 5, но от прошлого закрытия 100 до вершины 120 — все 20.
    assert true_range(candle, previous_close=100.0) == 20.0


def test_true_range_for_a_gap_down():
    candle = Candle(open_time=0, high=85.0, low=80.0, close=82.0,
                    quote_volume=1.0, trades=1)

    assert true_range(candle, previous_close=100.0) == 20.0


def test_normalized_true_range_makes_different_prices_comparable():
    """Один и тот же размах в процентах у дорогого и дешёвого инструмента
    должен давать одно и то же число."""
    expensive = Candle(open_time=0, high=64_640.0, low=63_360.0, close=64_000.0,
                       quote_volume=1.0, trades=1)
    cheap = Candle(open_time=0, high=0.505, low=0.495, close=0.5,
                   quote_volume=1.0, trades=1)

    assert normalized_true_range(expensive, 64_000.0) == pytest.approx(0.02)
    assert normalized_true_range(cheap, 0.5) == pytest.approx(0.02)


def test_ve_compares_current_range_with_the_usual_one():
    """База: свечи 99-101 при закрытии 100, то есть размах 2 % от цены.
    Текущая: 97-103, размах 6 %. Значит VE = 3."""
    highs = [101.0] * (WINDOW_CANDLES - 1) + [103.0]
    lows = [99.0] * (WINDOW_CANDLES - 1) + [97.0]

    result = compute_metrics(build_window(highs=highs, lows=lows))

    assert result.range_pct_median == pytest.approx(2.0)
    assert result.range_pct == pytest.approx(6.0)
    assert result.ve == pytest.approx(3.0)


def test_change_pct_is_signed():
    closes = [100.0] * (WINDOW_CANDLES - 1) + [104.0]

    result = compute_metrics(build_window(closes=closes))

    assert result.change_pct == pytest.approx(4.0)


def test_change_pct_is_negative_when_price_falls():
    closes = [100.0] * (WINDOW_CANDLES - 1) + [95.0]

    result = compute_metrics(build_window(closes=closes))

    assert result.change_pct == pytest.approx(-5.0)


def test_zero_baseline_median_gives_none_instead_of_crashing():
    """Инструмент с нулевым обычным оборотом делить не на что."""
    volumes = [1000.0] + [0.0] * BASELINE_CANDLES + [1000.0]

    assert compute_metrics(build_window(volumes=volumes)) is None
