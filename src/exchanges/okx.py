"""Клиент публичного API OKX Perpetual Swaps (USDT).

Используются только публичные эндпоинты, ключи API не нужны.
Контракт, которому следует модуль, описан в base.py.

Три отличия от Binance, которые пришлось учесть:

1. Об ошибке OKX сообщает не кодом HTTP, а полем code в теле ответа.
   raise_for_status такую ошибку не заметит, проверять надо отдельно.
2. Свечи приходят от новых к старым — порядок разворачивает клиент.
3. Числа сделок в публичном API нет ни в свечах, ни в тикере. Поэтому
   trades всюду None, RTC для OKX не считается, а score нормируется
   по двум метрикам (см. scoring.score).
"""

import requests

from src.exchanges.base import Candle, Instrument

NAME = "OKX"
MARKET = "USDT perpetual swap"
BASE_URL = "https://www.okx.com"
TIMEOUT_SEC = 15


def used_weight() -> int:
    """OKX не сообщает расход лимитов в заголовках ответа."""
    return 0


def _get(path: str, params: dict | None = None):
    """Один GET-запрос к публичному API OKX.

    Проверка поля code обязательна: при ошибке OKX отвечает кодом HTTP 200
    и сообщает о проблеме внутри тела. Без этой проверки код пошёл бы
    разбирать пустой data как нормальный ответ.
    """
    response = requests.get(BASE_URL + path, params=params, timeout=TIMEOUT_SEC)
    response.raise_for_status()

    body = response.json()
    if str(body.get("code")) != "0":
        raise RuntimeError(f"OKX ответил code={body.get('code')}: {body.get('msg')}")
    return body["data"]


def fetch_symbols() -> dict[str, str]:
    """Справочник бессрочных USDT-контрактов: instId -> статус торгов.

    instType=SWAP исключает спот и опционы на уровне запроса. Дополнительно
    отбираются линейные контракты с расчётами в USDT: у OKX есть ещё обратные
    (inverse), где залог и расчёт в монете, — это инструменты другой природы.
    """
    data = _get("/api/v5/public/instruments", {"instType": "SWAP"})
    return {
        item["instId"]: item["state"]
        for item in data
        if item.get("settleCcy") == "USDT" and item.get("ctType") == "linear"
    }


def fetch_tickers() -> dict[str, dict]:
    """24-часовая статистика по всем свопам сразу: instId -> сырые данные."""
    data = _get("/api/v5/market/tickers", {"instType": "SWAP"})
    return {item["instId"]: item for item in data}


def build_instruments(
    symbols: dict[str, str], tickers: dict[str, dict]
) -> list[Instrument]:
    """Соединить справочник со статистикой, отсортировать по убыванию оборота.

    Готового оборота в USDT у OKX нет. Есть объём в контрактах (vol24h) и
    в базовой валюте (volCcy24h); в USDT переводим по последней цене.
    Проверено на BTC-USDT-SWAP: volCcy24h × last совпало с оборотом,
    который биржа показывает в интерфейсе.

    Это приближение: строго правильно было бы умножать на среднюю цену
    за сутки, а не на последнюю. Для порога ликвидности точности хватает,
    в метриках эта величина не участвует.
    """
    instruments = []
    for symbol, status in symbols.items():
        ticker = tickers.get(symbol)
        if ticker is None:
            continue

        last = float(ticker["last"])
        opened = float(ticker["open24h"])

        instruments.append(
            Instrument(
                symbol=symbol,
                status=status,
                quote_volume_24h=float(ticker["volCcy24h"]) * last,
                trades_24h=None,
                last_price=last,
                change_pct_24h=(last - opened) / opened * 100 if opened else 0.0,
            )
        )
    instruments.sort(key=lambda instrument: instrument.quote_volume_24h, reverse=True)
    return instruments


def get_instruments() -> list[Instrument]:
    """Бессрочные USDT-свопы OKX с 24h-статистикой, по убыванию оборота."""
    return build_instruments(fetch_symbols(), fetch_tickers())


def fetch_server_time() -> int:
    """Текущее время биржи в миллисекундах UTC."""
    return int(_get("/api/v5/public/time")[0]["ts"])


def fetch_raw_candles(
    symbol: str, interval: str, limit: int, end_time: int | None = None
) -> list:
    """Сырой ответ /api/v5/market/candles.

    Параметр after отбирает свечи строго раньше указанного времени, поэтому
    к границе прибавляется миллисекунда — иначе сама граничная свеча
    в ответ не попадёт. Проверено на живом API.
    """
    params = {"instId": symbol, "bar": interval, "limit": limit}
    if end_time is not None:
        params["after"] = end_time + 1
    return _get("/api/v5/market/candles", params)


def raw_candles_oldest_first(raw: list) -> list:
    """OKX отдаёт свечи от новых к старым — разворачиваем.

    Порядок приводит клиент, а не общий код: остальные модули не должны
    знать, чем отличаются ответы разных бирж.
    """
    return list(reversed(raw))


def parse_candle(raw: list) -> Candle:
    """Разобрать один элемент ответа /api/v5/market/candles.

    Девять значений без имён:
        0 ts, 1 open, 2 high, 3 low, 4 close,
        5 объём в контрактах, 6 объём в базовой валюте,
        7 оборот в quote-валюте (USDT), 8 признак закрытия свечи.

    Берётся индекс 7 — оборот сразу в USDT, пересчитывать не нужно.
    Числа сделок в ответе нет, поэтому trades всегда None.
    """
    return Candle(
        open_time=int(raw[0]),
        high=float(raw[2]),
        low=float(raw[3]),
        close=float(raw[4]),
        quote_volume=float(raw[7]),
        trades=None,
    )
