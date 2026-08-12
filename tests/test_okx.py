"""Тесты клиента OKX. Сеть не используется.

Фикстуры — куски настоящих ответов API, снятые при разведке.
"""

from src.exchanges.okx import (
    build_instruments,
    parse_candle,
    raw_candles_oldest_first,
)

# Реальный элемент ответа /api/v5/market/candles: девять значений без имён.
RAW_CANDLE = [
    "1786576500000",   # 0 время открытия
    "63391.9",         # 1 open
    "63419.6",         # 2 high
    "63352",           # 3 low
    "63377.7",         # 4 close
    "22774.58",        # 5 объём в контрактах
    "227.7458",        # 6 объём в базовой валюте (BTC)
    "14434802.32664",  # 7 оборот в USDT
    "1",               # 8 свеча закрыта
]

BTC_TICKER = {
    "instId": "BTC-USDT-SWAP",
    "last": "63377.8",
    "open24h": "63561.5",
    "volCcy24h": "63971.7298",
    "vol24h": "6397172.98",
}


def test_turnover_is_converted_to_usdt():
    """Готового оборота в USDT у OKX нет: volCcy24h — в базовой валюте.
    Проверено на живых данных, что volCcy24h × last даёт то же число,
    что биржа показывает в интерфейсе."""
    result = build_instruments({"BTC-USDT-SWAP": "live"}, {"BTC-USDT-SWAP": BTC_TICKER})

    assert len(result) == 1
    assert result[0].quote_volume_24h == 63971.7298 * 63377.8


def test_change_is_computed_from_the_daily_open():
    """Готового процента изменения OKX не отдаёт, только цену открытия суток."""
    result = build_instruments({"BTC-USDT-SWAP": "live"}, {"BTC-USDT-SWAP": BTC_TICKER})

    expected = (63377.8 - 63561.5) / 63561.5 * 100
    assert result[0].change_pct_24h == expected


def test_trade_count_is_absent_not_zero():
    """OKX не публикует число сделок. None означает «не измерено»;
    ноль означал бы «сделок не было» — это разные вещи, и путать их нельзя:
    от этого зависит, считается RTC или нет."""
    result = build_instruments({"BTC-USDT-SWAP": "live"}, {"BTC-USDT-SWAP": BTC_TICKER})

    assert result[0].trades_24h is None


def test_candle_takes_turnover_in_usdt():
    candle = parse_candle(RAW_CANDLE)

    assert candle.open_time == 1786576500000
    assert candle.high == 63419.6
    assert candle.low == 63352.0
    assert candle.close == 63377.7
    # Индекс 7, а не 5 (контракты) и не 6 (BTC).
    assert candle.quote_volume == 14434802.32664
    assert candle.trades is None


def test_candles_are_reversed_to_oldest_first():
    """OKX отдаёт свечи от новых к старым, остальной код ждёт обратного."""
    newest_first = [["300"], ["200"], ["100"]]

    assert raw_candles_oldest_first(newest_first) == [["100"], ["200"], ["300"]]
