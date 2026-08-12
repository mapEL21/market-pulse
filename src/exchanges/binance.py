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


_used_weight_1m = 0


def used_weight() -> int:
    """Вес, израсходованный за последнюю минуту, из заголовка последнего ответа.

    Хранится в переменной модуля: это диагностика, а не данные, прогон
    однопоточный, и таскать значение через все вызовы ради одной строки
    в отчёте было бы дороже, чем оно того стоит.
    """
    return _used_weight_1m


def _get(path: str, params: dict | None = None):
    """Выполнить один GET-запрос к публичному API и вернуть разобранный JSON."""
    global _used_weight_1m

    response = requests.get(BASE_URL + path, params=params, timeout=TIMEOUT_SEC)

    # Заголовок приходит и с ошибочными ответами, поэтому читаем до проверки.
    header = response.headers.get("X-MBX-USED-WEIGHT-1M")
    if header is not None:
        _used_weight_1m = int(header)

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


@dataclass
class Candle:
    """Одна свеча. Поля — только те, что используются формулами раздела 6 spec.md."""

    open_time: int        # начало свечи, миллисекунды UTC
    high: float
    low: float
    close: float
    quote_volume: float   # оборот за свечу в USDT
    trades: int           # число сделок за свечу


def parse_candle(raw: list) -> Candle:
    """Разобрать один элемент ответа /fapi/v1/klines.

    Ответ приходит массивом из двенадцати значений без имён, поэтому поля
    берутся по индексам из документации Binance. Индекс 7 — оборот в
    quote-валюте, то есть в USDT; индекс 5 — тот же объём, но в монетах,
    и он здесь не нужен: по разделу 4 spec.md объём считается в USDT,
    иначе инструменты с разной ценой несравнимы.
    """
    return Candle(
        open_time=int(raw[0]),
        high=float(raw[2]),
        low=float(raw[3]),
        close=float(raw[4]),
        quote_volume=float(raw[7]),
        trades=int(raw[8]),
    )


def fetch_server_time() -> int:
    """Текущее время биржи в миллисекундах UTC.

    Нужно, чтобы граница последней закрытой свечи не зависела от того,
    насколько точно идут часы на машине, где запущена программа.
    """
    return int(_get("/fapi/v1/time")["serverTime"])


def fetch_raw_klines(
    symbol: str, interval: str, limit: int, end_time: int | None = None
) -> list:
    """Сырой ответ /fapi/v1/klines — массивы без имён, как их отдаёт биржа.

    Отдельно от fetch_klines, потому что в кэш кладётся именно сырой ответ:
    тогда формат кэша не зависит от того, какие поля мы решим разбирать.

    end_time — время открытия последней нужной свечи. Без него биржа отдаёт
    последние limit свечей «на момент запроса», и результат зависит от того,
    на какой секунде прогона до инструмента дошла очередь: прогон длиннее
    одной свечи сдвигает окно у поздних инструментов. С end_time ответ
    одинаков независимо от момента запроса.
    """
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time is not None:
        params["endTime"] = end_time
    return _get("/fapi/v1/klines", params)


def fetch_klines(
    symbol: str, interval: str, limit: int, end_time: int | None = None
) -> list[Candle]:
    """Свечи одного инструмента, от старых к новым."""
    return [
        parse_candle(item)
        for item in fetch_raw_klines(symbol, interval, limit, end_time)
    ]
