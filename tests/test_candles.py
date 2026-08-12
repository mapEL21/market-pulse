"""Тесты работы со свечами: арифметика границ и разбор ответа Binance.

Сеть не используется. Все проверяемые функции чистые.
"""

from datetime import datetime, timezone

from src.candles import (
    BASELINE_CANDLES,
    INTERVAL_MS,
    WINDOW_CANDLES,
    analysis_window,
    closed_candles,
    last_closed_open_time,
)
from src.exchanges.binance import Candle, parse_candle


def ms(hour: int, minute: int, second: int = 0) -> int:
    """Миллисекунды UTC для указанного времени 12 августа 2026 года."""
    moment = datetime(2026, 8, 12, hour, minute, second, tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def make_candle(open_time: int) -> Candle:
    """Свеча с нужным временем открытия; остальные поля для теста неважны."""
    return Candle(
        open_time=open_time,
        high=1.0,
        low=1.0,
        close=1.0,
        quote_volume=1.0,
        trades=1,
    )


# Реальный по структуре ответ /fapi/v1/klines: двенадцать значений без имён.
RAW_KLINE = [
    1755000000000,  # 0  время открытия
    "63700.0",      # 1  open
    "63850.5",      # 2  high
    "63650.1",      # 3  low
    "63800.2",      # 4  close
    "125.345",      # 5  объём в монетах — не он нужен
    1755000899999,  # 6  время закрытия
    "7991234.56",   # 7  оборот в USDT
    3421,           # 8  число сделок
    "60.1",         # 9  taker buy, монеты
    "3830000.0",    # 10 taker buy, USDT
    "0",            # 11 не используется
]


def test_baseline_window_is_derived_from_hours():
    """48 часов на 15m — 192 свечи базы, плюс анализируемая, плюс одна перед
    окном как источник Close_(i-1) для первой True Range."""
    assert BASELINE_CANDLES == 192
    assert WINDOW_CANDLES == 194


def test_boundary_in_the_middle_of_a_candle():
    """14:07:33 — идёт свеча 14:00-14:15, последняя закрытая открылась в 13:45."""
    assert last_closed_open_time(ms(14, 7, 33)) == ms(13, 45)


def test_boundary_exactly_on_the_mark():
    """14:15:00.000 — свеча 14:15 только что открылась и ещё не закрыта,
    последняя закрытая открылась в 14:00."""
    assert last_closed_open_time(ms(14, 15)) == ms(14, 0)


def test_boundary_one_millisecond_before_the_mark():
    """За миллисекунду до 14:15 свеча 14:00 ещё идёт — последняя закрытая 13:45."""
    assert last_closed_open_time(ms(14, 15) - 1) == ms(13, 45)


def test_forming_candle_is_dropped():
    candles = [make_candle(ms(13, 45)), make_candle(ms(14, 0))]

    result = closed_candles(candles, last_closed_open=ms(13, 45))

    assert [candle.open_time for candle in result] == [ms(13, 45)]


def test_nothing_is_dropped_when_forming_candle_is_absent():
    """У неликвидного инструмента текущей свечи в ответе может не быть.
    Отбрасывать в этом случае нечего — иначе потеряли бы закрытую свечу."""
    candles = [make_candle(ms(13, 30)), make_candle(ms(13, 45))]

    result = closed_candles(candles, last_closed_open=ms(13, 45))

    assert [candle.open_time for candle in result] == [ms(13, 30), ms(13, 45)]


def test_analysis_window_trims_extra_candles_from_the_start():
    """Если свечей пришло больше нужного, лишние отрезаются от начала:
    базовое окно должно быть ровно 192 свечи, иначе поедет медиана."""
    boundary = ms(14, 0)
    first_open = boundary - (WINDOW_CANDLES + 4) * INTERVAL_MS
    candles = [
        make_candle(first_open + step * INTERVAL_MS)
        for step in range(WINDOW_CANDLES + 5)
    ]

    window = analysis_window(candles, boundary)

    assert len(window) == WINDOW_CANDLES
    assert window[-1].open_time == boundary
    assert window[0].open_time == boundary - (WINDOW_CANDLES - 1) * INTERVAL_MS


def test_analysis_window_leaves_short_history_short():
    """Инструмент моложе базового окна не должен «дополняться» — его отсеет
    проверка длины в load_candles."""
    boundary = ms(14, 0)
    candles = [make_candle(boundary - step * INTERVAL_MS) for step in range(9, -1, -1)]

    window = analysis_window(candles, boundary)

    assert len(window) == 10


def test_parse_candle_takes_turnover_in_usdt_not_in_coins():
    candle = parse_candle(RAW_KLINE)

    assert candle.open_time == 1755000000000
    assert candle.high == 63850.5
    assert candle.low == 63650.1
    assert candle.close == 63800.2
    assert candle.trades == 3421
    # Индекс 7, а не 5: 7 991 234.56 USDT, а не 125.345 монет.
    assert candle.quote_volume == 7991234.56
