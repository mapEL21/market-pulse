"""Тесты клиента Binance.

Проверяется только build_instruments — единственная функция модуля, в которой
есть логика. Сеть не используется: на вход подаются такие же структуры, какие
возвращают fetch_symbols и fetch_tickers.
"""

from src.exchanges.binance import build_instruments

# Числа в ответе Binance приходят строками, а count — целым числом.
# Фикстура повторяет это, чтобы тест проверял реальное преобразование типов.
BTC_TICKER = {
    "quoteVolume": "1000.5",
    "count": 42,
    "lastPrice": "64000.1",
    "priceChangePercent": "-1.25",
}
ALT_TICKER = {
    "quoteVolume": "5000.0",
    "count": 7,
    "lastPrice": "0.5",
    "priceChangePercent": "3.0",
}


def test_fields_are_converted_to_numbers():
    result = build_instruments({"BTCUSDT": "TRADING"}, {"BTCUSDT": BTC_TICKER})

    assert len(result) == 1
    btc = result[0]
    assert btc.symbol == "BTCUSDT"
    assert btc.status == "TRADING"
    assert btc.quote_volume_24h == 1000.5
    assert btc.trades_24h == 42
    assert btc.last_price == 64000.1
    assert btc.change_pct_24h == -1.25


def test_sorted_by_turnover_descending():
    symbols = {"BTCUSDT": "TRADING", "ALTUSDT": "TRADING"}
    tickers = {"BTCUSDT": BTC_TICKER, "ALTUSDT": ALT_TICKER}

    result = build_instruments(symbols, tickers)

    # ALTUSDT с оборотом 5000 должен оказаться выше BTCUSDT с оборотом 1000.5,
    # хотя в исходном словаре он был вторым.
    assert [instrument.symbol for instrument in result] == ["ALTUSDT", "BTCUSDT"]


def test_symbol_without_ticker_is_skipped():
    symbols = {"BTCUSDT": "TRADING", "GHOSTUSDT": "TRADING"}
    tickers = {"BTCUSDT": BTC_TICKER}

    result = build_instruments(symbols, tickers)

    assert [instrument.symbol for instrument in result] == ["BTCUSDT"]
