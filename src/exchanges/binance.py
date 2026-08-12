"""Клиент публичного API Binance USDⓈ-M Futures.

Используются только публичные эндпоинты, ключи API не нужны.
Модуль ничего не печатает: он возвращает данные, вывод — задача main.py.
"""

from dataclasses import dataclass

import requests

BASE_URL = "https://fapi.binance.com"
TIMEOUT_SEC = 10


@dataclass
class Instrument:
    """Инструмент биржи вместе с его 24-часовой статистикой."""

    symbol: str
    status: str               # статус торгов, например TRADING
    quote_volume_24h: float   # оборот за 24 ч в USDT
    trades_24h: int           # число сделок за 24 ч
    last_price: float
    change_pct_24h: float


def _get(path: str):
    """Выполнить один GET-запрос к публичному API и вернуть разобранный JSON."""
    response = requests.get(BASE_URL + path, timeout=TIMEOUT_SEC)
    response.raise_for_status()
    return response.json()


def fetch_symbols() -> dict[str, str]:
    """Справочник бессрочных USDT-контрактов: symbol -> статус торгов.

    Отбор по contractType и quoteAsset — это не фильтр из раздела 5 spec.md,
    а определение того, какие инструменты вообще рассматриваются: на USDⓈ-M
    торгуются ещё квартальные поставочные фьючерсы и USDC-контракты.
    """
    data = _get("/fapi/v1/exchangeInfo")
    return {
        item["symbol"]: item["status"]
        for item in data["symbols"]
        if item["contractType"] == "PERPETUAL" and item["quoteAsset"] == "USDT"
    }


def fetch_tickers() -> dict[str, dict]:
    """24-часовая статистика по всем инструментам сразу: symbol -> сырые данные.

    Эндпоинт вызывается без параметра symbol и отдаёт весь рынок одним ответом.
    Иначе понадобилось бы ~400 запросов и упор в rate limit.
    """
    data = _get("/fapi/v1/ticker/24hr")
    return {item["symbol"]: item for item in data}


def build_instruments(
    symbols: dict[str, str], tickers: dict[str, dict]
) -> list[Instrument]:
    """Соединить справочник со статистикой, отсортировать по убыванию оборота.

    Чистая функция без обращения к сети — именно она покрыта тестами.
    """
    instruments = []
    for symbol, status in symbols.items():
        ticker = tickers.get(symbol)
        if ticker is None:
            # Инструмент есть в справочнике, но статистики по нему нет.
            continue
        instruments.append(
            Instrument(
                symbol=symbol,
                status=status,
                quote_volume_24h=float(ticker["quoteVolume"]),
                trades_24h=int(ticker["count"]),
                last_price=float(ticker["lastPrice"]),
                change_pct_24h=float(ticker["priceChangePercent"]),
            )
        )
    instruments.sort(key=lambda instrument: instrument.quote_volume_24h, reverse=True)
    return instruments


def get_instruments() -> list[Instrument]:
    """Бессрочные USDT-контракты Binance с 24h-статистикой, по убыванию оборота."""
    return build_instruments(fetch_symbols(), fetch_tickers())
