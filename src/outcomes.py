"""Что произошло с ценой после попадания инструмента в список.

Формулы — раздел 9.1 spec.md. Именно эта таблица превращает инструмент
в исследование: без неё пороги и веса остаются недоказуемыми гипотезами.

Модуль ничего не знает про базу и про сеть: на входе цена и свечи,
на выходе числа.
"""

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from src.candles import INTERVAL, PAUSE_SEC
from src.exchanges import binance, okx
from src.exchanges.base import Candle
from src.storage import (
    connect,
    from_iso,
    ripe_observations,
    save_outcome,
    to_iso,
)

MINUTE_MS = 60 * 1000
HALF_HOUR_MS = 30 * MINUTE_MS
TWO_HOURS_MS = 120 * MINUTE_MS
EIGHT_HOURS_MS = 480 * MINUTE_MS

# Через сколько после анализируемой свечи кандидат считается созревшим:
# свеча T+8h должна успеть закрыться.
RIPE_AFTER_MS = EIGHT_HOURS_MS + 15 * MINUTE_MS

# Свечей нужно 33 — от T до T+8h включительно. Просим с запасом, лишние
# отсеются по времени открытия.
FETCH_LIMIT = 40

CLIENTS = {client.NAME: client for client in (binance, okx)}


@dataclass
class Outcome:
    """Результат кандидата. None означает «свечи не нашлось», а не «ноль»."""

    ret_30m: float | None
    ret_2h: float | None
    ret_8h: float | None
    max_move_2h: float | None


def compute_outcome(
    price: float, candle_open_ms: int, future: list[Candle]
) -> Outcome:
    """Посчитать результат по свечам, наступившим после анализируемой.

    price — цена закрытия анализируемой свечи, точка отсчёта.
    candle_open_ms — её время открытия, T.
    future — свечи начиная с T; лишние не мешают, недостающие дают None.
    """
    by_open_time = {candle.open_time: candle for candle in future}

    def return_after(offset_ms: int) -> float | None:
        candle = by_open_time.get(candle_open_ms + offset_ms)
        if candle is None or price == 0:
            return None
        return (candle.close - price) / price * 100

    return Outcome(
        ret_30m=return_after(HALF_HOUR_MS),
        ret_2h=return_after(TWO_HOURS_MS),
        ret_8h=return_after(EIGHT_HOURS_MS),
        max_move_2h=_max_move(price, candle_open_ms, future),
    )


def _max_move(
    price: float, candle_open_ms: int, future: list[Candle]
) -> float | None:
    """Максимальное отклонение в любую сторону за два часа, в процентах.

    Берутся свечи с открытием в (T, T+2h]: сама анализируемая свеча в окно
    не входит, её движение уже учтено в метриках.

    Знака у величины нет — она отвечает на вопрос «была ли амплитуда».
    Отрицательной получиться не может: минимум окна не бывает выше максимума,
    поэтому хотя бы одно из двух отклонений неотрицательно.
    """
    window = [
        candle
        for candle in future
        if candle_open_ms < candle.open_time <= candle_open_ms + TWO_HOURS_MS
    ]
    if not window or price == 0:
        return None

    up = (max(candle.high for candle in window) - price) / price
    down = (price - min(candle.low for candle in window)) / price
    return max(up, down) * 100


@dataclass
class FillStats:
    """Итоги заполнения."""

    ripe: int        # созревших наблюдений найдено
    filled: int      # записано результатов
    fetched: int     # запросов к биржам (одна выборка на инструмент и свечу)
    no_data: int     # свечей не нашлось: делистинг или пропуск в истории


def fetch_future_candles(client, symbol: str, candle_open_ms: int) -> list[Candle]:
    """Свечи от анализируемой до T+8h включительно."""
    raw = client.fetch_raw_candles(
        symbol, INTERVAL, FETCH_LIMIT, end_time=candle_open_ms + EIGHT_HOURS_MS
    )
    candles = [
        client.parse_candle(item) for item in client.raw_candles_oldest_first(raw)
    ]
    return [candle for candle in candles if candle.open_time >= candle_open_ms]


def fill_outcomes(connection: sqlite3.Connection, now_ms: int) -> FillStats:
    """Дописать результаты всем созревшим кандидатам.

    Свечи кэшируются на время работы функции по ключу «биржа, инструмент,
    свеча»: один и тот же кандидат может встретиться в нескольких прогонах
    одной свечи, и качать для него историю дважды незачем.
    """
    ripe_before = to_iso(now_ms - RIPE_AFTER_MS)
    rows = ripe_observations(connection, ripe_before)

    seen: dict[tuple[str, str, int], list[Candle]] = {}
    filled = 0
    no_data = 0

    for observation_id, exchange, symbol, price, candle_time in rows:
        candle_open_ms = from_iso(candle_time)
        key = (exchange, symbol, candle_open_ms)

        if key not in seen:
            seen[key] = fetch_future_candles(
                CLIENTS[exchange], symbol, candle_open_ms
            )
            time.sleep(PAUSE_SEC)

        outcome = compute_outcome(price, candle_open_ms, seen[key])
        if outcome.ret_8h is None:
            # Инструмент делистнули или в истории пропуск. Записывать нечего:
            # строка из одних NULL хуже отсутствия строки — она означала бы,
            # что результат посчитан и оказался пустым.
            no_data += 1
            continue

        save_outcome(
            connection,
            observation_id,
            outcome.ret_30m,
            outcome.ret_2h,
            outcome.ret_8h,
            outcome.max_move_2h,
        )
        filled += 1

    return FillStats(
        ripe=len(rows), filled=filled, fetched=len(seen), no_data=no_data
    )


def main() -> None:
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    print(f"Заполнение outcomes, время UTC {to_iso(now_ms)}")
    print(f"Созревшими считаются кандидаты со свечой до {to_iso(now_ms - RIPE_AFTER_MS)}")

    connection = connect()
    try:
        stats = fill_outcomes(connection, now_ms)
    finally:
        connection.close()

    print()
    print(f"Найдено созревших : {stats.ripe}")
    print(f"Запросов к биржам : {stats.fetched}")
    print(f"Записано          : {stats.filled}")
    print(f"Без данных        : {stats.no_data}")


if __name__ == "__main__":
    main()
