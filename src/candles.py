"""Работа со свечами: границы времени и приведение ответа биржи к базе расчётов.

Раздел 4 spec.md фиксирует два соглашения, которые реализованы здесь:

1. Базовое окно задаётся в часах, а не в числе свечей. Поэтому число свечей
   ниже вычисляется из длины окна, а не написано константой: при переходе
   на 5m достаточно поменять INTERVAL, и 576 получится само.
2. Текущая формирующаяся свеча в расчётах не участвует. Граница считается
   один раз за прогон по времени биржи и применяется ко всем инструментам,
   чтобы все они анализировались на одной и той же свече.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path

from src.exchanges.base import Candle, Instrument

INTERVAL = "15m"
INTERVAL_MS = 15 * 60 * 1000

BASELINE_HOURS = 48
BASELINE_CANDLES = BASELINE_HOURS * 60 * 60 * 1000 // INTERVAL_MS  # 192 на 15m

# Базовое окно, анализируемая свеча и ещё одна свеча перед окном.
# Самая ранняя в расчёты не входит: она нужна только как Close_(i-1) для
# True Range первой свечи базового окна (раздел 6.3 spec.md). Без неё медиана
# TR считалась бы по 191 значению, а RVOL и RTC — по 192, и метрики одного
# инструмента опирались бы на окна разной длины.
WINDOW_CANDLES = BASELINE_CANDLES + 2

# Небольшой запас сверх нужного: если биржа по какой-то причине вернёт
# лишнюю свечу за границей, analysis_window обрежет список до нужной длины.
FETCH_LIMIT = WINDOW_CANDLES + 1

CACHE_DIR = Path("data") / "cache"

# Пауза между запросами к бирже. Маленькая осознанно: вес запроса невелик,
# а фактический расход проверяется через client.used_weight().
PAUSE_SEC = 0.05


def last_closed_open_time(now_ms: int, interval_ms: int = INTERVAL_MS) -> int:
    """Время открытия последней полностью закрытой свечи, в миллисекундах UTC.

    Считается арифметически, а не как «последний элемент ответа минус один».
    У неликвидного инструмента текущей свечи в ответе может не оказаться
    вовсе — если в интервале не было сделок, — и тогда отбрасывание
    последнего элемента молча сдвинуло бы анализ на свечу назад.
    """
    current_open = (now_ms // interval_ms) * interval_ms
    return current_open - interval_ms


def closed_candles(candles: list[Candle], last_closed_open: int) -> list[Candle]:
    """Оставить только свечи, закрывшиеся к моменту границы."""
    return [candle for candle in candles if candle.open_time <= last_closed_open]


def analysis_window(candles: list[Candle], last_closed_open: int) -> list[Candle]:
    """Ровно WINDOW_CANDLES свечей, последняя из которых — граничная.

    Обрезка сверху обязательна, а не для красоты: базовое окно по разделу 4
    spec.md — ровно 192 свечи. Лишняя свеча в начале сместила бы медиану,
    и метрики двух инструментов считались бы по окнам разной длины.
    """
    return closed_candles(candles, last_closed_open)[-WINDOW_CANDLES:]


@dataclass
class LoadStats:
    """Итоги загрузки свечей за прогон."""

    requested: int      # сколько инструментов пришло с этапа фильтров
    from_cache: int     # взято из кэша, без запроса к бирже
    from_api: int       # скачано
    too_short: int      # отсеяно: истории меньше WINDOW_CANDLES
    loaded: int         # готово к расчёту метрик
    elapsed_sec: float
    used_weight: int    # израсходованный вес за минуту, по данным биржи


def cache_path(exchange: str, symbol: str, last_closed_open: int) -> Path:
    """Путь к кэшу ответа для инструмента и конкретной свечи.

    Граница входит в имя файла, поэтому кэш протухает сам: наступила новая
    свеча — имя другое, файла нет, идём в сеть. Ни времени жизни, ни очистки.

    Имя биржи в ключе обязательно: один и тот же тикер торгуется на обеих,
    а ответы у них разного формата.
    """
    return CACHE_DIR / f"{exchange}_{symbol}_{INTERVAL}_{last_closed_open}.json"


def load_symbol_candles(
    client, symbol: str, last_closed_open: int
) -> tuple[list[Candle], bool]:
    """Закрытые свечи одного инструмента.

    client — модуль биржи, контракт описан в exchanges/base.py.

    Второй элемент пары — был ли запрос к бирже. Нужен, чтобы не делать паузу
    после попадания в кэш и чтобы посчитать статистику прогона.
    """
    path = cache_path(client.NAME, symbol, last_closed_open)

    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        went_to_api = False
    else:
        raw = client.fetch_raw_candles(
            symbol, INTERVAL, FETCH_LIMIT, end_time=last_closed_open
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw), encoding="utf-8")
        went_to_api = True

    ordered = client.raw_candles_oldest_first(raw)
    candles = [client.parse_candle(item) for item in ordered]
    return analysis_window(candles, last_closed_open), went_to_api


def load_candles(
    client, instruments: list[Instrument], last_closed_open: int
) -> tuple[dict[str, list[Candle]], LoadStats]:
    """Загрузить свечи для всех инструментов и отсеять тех, у кого мало истории.

    Возвращает словарь symbol -> свечи и статистику прогона. Инструменты
    с неполным базовым окном в словарь не попадают: по разделу 5 spec.md
    метрика по неполной истории недостоверна.
    """
    started = time.monotonic()
    by_symbol: dict[str, list[Candle]] = {}
    from_cache = 0
    from_api = 0
    too_short = 0

    for instrument in instruments:
        candles, went_to_api = load_symbol_candles(
            client, instrument.symbol, last_closed_open
        )

        if went_to_api:
            from_api += 1
            time.sleep(PAUSE_SEC)
        else:
            from_cache += 1

        if len(candles) < WINDOW_CANDLES:
            too_short += 1
            continue

        by_symbol[instrument.symbol] = candles

    stats = LoadStats(
        requested=len(instruments),
        from_cache=from_cache,
        from_api=from_api,
        too_short=too_short,
        loaded=len(by_symbol),
        elapsed_sec=time.monotonic() - started,
        used_weight=client.used_weight(),
    )
    return by_symbol, stats
