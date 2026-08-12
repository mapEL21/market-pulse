"""Общие типы бирж и контракт, которому обязан следовать клиент.

Структуры лежат здесь, а не в модуле конкретной биржи, потому что ими
пользуются filters, candles, metrics и report — модули, которым всё равно,
откуда пришли данные.

Клиент биржи — это обычный модуль (src/exchanges/binance.py, okx.py),
который передаётся параметром. В Python модуль — такой же объект, и заводить
ради двух бирж иерархию классов ни к чему.

Модуль-клиент обязан предоставлять:

    NAME: str
        Короткое имя биржи заглавными: 'BINANCE', 'OKX'. Попадает в имя файла
        кэша и в колонку exchange таблицы candidates.

    MARKET: str
        Как называется рынок на этой бирже: 'USDT-M perpetual',
        'USDT perpetual swap'. Только для отчёта — чтобы из вывода было
        видно, что это фьючерсы, а не спот.

    get_instruments() -> list[Instrument]
        Бессрочные USDT-контракты со статистикой за 24 часа.

    fetch_server_time() -> int
        Текущее время биржи в миллисекундах UTC.

    fetch_raw_candles(symbol, interval, limit, end_time) -> list
        Сырой ответ биржи со свечами, как он пришёл. В кэш кладётся именно он.

    parse_candle(raw) -> Candle
        Разбор одного элемента этого ответа.

    raw_candles_oldest_first(raw) -> list
        Сырые свечи в порядке от старых к новым. Binance так и отдаёт,
        OKX — наоборот, и приводить порядок должен клиент: остальной код
        не обязан знать о таких различиях.

    used_weight() -> int
        Диагностика расхода лимитов за последнюю минуту. Если биржа такого
        не сообщает, клиент возвращает 0.
"""

from dataclasses import dataclass


@dataclass
class Instrument:
    """Инструмент биржи вместе с его 24-часовой статистикой."""

    symbol: str
    status: str               # статус торгов, как его называет биржа
    quote_volume_24h: float   # оборот за 24 ч в USDT
    trades_24h: int | None    # число сделок за 24 ч; None, если биржа не отдаёт
    last_price: float
    change_pct_24h: float


@dataclass
class Candle:
    """Одна свеча. Поля — только те, что используются формулами раздела 6 spec.md."""

    open_time: int              # начало свечи, миллисекунды UTC
    high: float
    low: float
    close: float
    quote_volume: float         # оборот за свечу в USDT
    trades: int | None          # число сделок; None, если биржа не отдаёт
