"""Исторический бэктест: прогоны по прошедшим свечам.

Смысл: свечи за прошлое биржи отдают, значит прошедшие сутки можно проиграть
как последовательность прогонов и сразу заполнить outcomes — «будущее» для
этих свечей уже произошло. Сутки дают 96 прогонов на биржу вместо одного
прогона в 15 минут вживую.

Главная опасность — заглядывание в будущее: если в расчёт метрик на момент T
попадут свечи позже T, результаты получатся блестящими и фальшивыми. Защита
устроена так:

* нарезка окна — отдельная функция window_at, и тест проверяет ровно то,
  что в окне нет ни одной свечи позже T;
* нарезка отделена от расчёта: compute_metrics получает готовый срез
  и физически не может дотянуться до остальной истории;
* используется тот же analysis_window, что и в живом режиме, — никакого
  отдельного «упрощённого расчёта для бэктеста».
"""

import sqlite3
import time
from dataclasses import dataclass, replace

from src.candles import (
    INTERVAL,
    INTERVAL_MS,
    PAUSE_SEC,
    WINDOW_CANDLES,
    analysis_window,
    last_closed_open_time,
)
from src.config import DEFAULT
from src.exchanges import binance, okx
from src.exchanges.base import Candle, Instrument
from src.filters import apply_filters, rejection_reason
from src.metrics import compute_metrics
from src.outcomes import EIGHT_HOURS_MS, compute_outcome
from src.scoring import is_candidate, select_candidates
from src.storage import (
    candidate_ids,
    connect,
    save_candidates,
    save_outcome,
    save_run,
    to_iso,
)

EXCHANGES = (binance, okx)
SOURCE = "backtest"

# Сколько прогонов моделируем: сутки с шагом в свечу.
DEFAULT_RUNS = 96

# Сколько свечей нужно после анализируемой, чтобы посчитать outcomes.
FUTURE_CANDLES = EIGHT_HOURS_MS // INTERVAL_MS

# Свечей в сутках — по ним считается оборот за 24 часа.
CANDLES_PER_DAY = 24 * 60 * 60 * 1000 // INTERVAL_MS


def run_times(last_candle_open: int, count: int) -> list[int]:
    """Времена открытия свечей, по которым будут прогоны, от старых к новым."""
    return [
        last_candle_open - step * INTERVAL_MS for step in reversed(range(count))
    ]


def history_span(run_moments: list[int]) -> tuple[int, int]:
    """Границы истории, нужной для всех прогонов: (первая свеча, последняя).

    Слева — базовое окно самого раннего прогона, справа — горизонт outcomes
    самого позднего.
    """
    return (
        run_moments[0] - (WINDOW_CANDLES - 1) * INTERVAL_MS,
        run_moments[-1] + FUTURE_CANDLES * INTERVAL_MS,
    )


def fetch_history(client, symbol: str, start_ms: int, end_ms: int) -> list[Candle]:
    """Свечи инструмента за период [start_ms, end_ms], от старых к новым.

    Биржи отдают ограниченное число свечей за запрос — 1500 у Binance,
    300 у OKX, — поэтому история набирается страницами от конца к началу:
    каждый следующий запрос заканчивается свечой перед самой ранней
    из уже полученных.

    Свечи складываются в словарь по времени открытия: страницы могут
    перекрываться, и так дубли устраняются сами.
    """
    by_open_time: dict[int, Candle] = {}
    cursor = end_ms

    while cursor >= start_ms:
        raw = client.fetch_raw_candles(
            symbol, INTERVAL, client.MAX_CANDLES_PER_REQUEST, end_time=cursor
        )
        page = [
            client.parse_candle(item)
            for item in client.raw_candles_oldest_first(raw)
        ]
        if not page:
            break

        for candle in page:
            by_open_time[candle.open_time] = candle

        oldest = min(candle.open_time for candle in page)
        if oldest <= start_ms:
            break

        # Курсор обязан убывать, иначе цикл не кончится: если биржа вернула
        # страницу целиком новее курсора, сдвигаем его сами.
        cursor = min(oldest, cursor) - INTERVAL_MS
        time.sleep(PAUSE_SEC)

    return [
        by_open_time[open_time]
        for open_time in sorted(by_open_time)
        if start_ms <= open_time <= end_ms
    ]


def window_at(history: list[Candle], candle_open_ms: int) -> list[Candle]:
    """Окно для расчёта метрик на момент candle_open_ms.

    Возвращает пустой список, если полного окна в истории нет: неполное окно
    дало бы медиану по меньшему числу свечей, и метрики разных прогонов
    оказались бы несравнимы.

    Используется тот же analysis_window, что и в живом режиме. Проверка
    на совпадение последней свечи с запрошенной обязательна: без неё
    при пропуске в истории окно закончилось бы более ранней свечой,
    и прогон молча посчитался бы не на той свече.
    """
    window = analysis_window(history, candle_open_ms)
    if len(window) < WINDOW_CANDLES or window[-1].open_time != candle_open_ms:
        return []
    return window


def future_at(history: list[Candle], candle_open_ms: int) -> list[Candle]:
    """Свечи от анализируемой до T+8h включительно — для расчёта outcomes."""
    return [
        candle
        for candle in history
        if candle_open_ms <= candle.open_time <= candle_open_ms + EIGHT_HOURS_MS
    ]


def _day_window(history: list[Candle], candle_open_ms: int) -> list[Candle]:
    """Свечи последних суток, заканчивая анализируемой."""
    earliest = candle_open_ms - (CANDLES_PER_DAY - 1) * INTERVAL_MS
    return [
        candle
        for candle in history
        if earliest <= candle.open_time <= candle_open_ms
    ]


def turnover_24h(history: list[Candle], candle_open_ms: int) -> float:
    """Оборот за 24 часа на момент T, посчитанный по свечам.

    В живом режиме эта величина берётся из 24-часового тикера, но тикер
    показывает только «сейчас» — для свечи суточной давности его нет.
    Сумма по 96 свечам даёт близкое, но не идентичное число; расхождение
    зафиксировано в spec.md.
    """
    return sum(candle.quote_volume for candle in _day_window(history, candle_open_ms))


def trades_24h(history: list[Candle], candle_open_ms: int) -> int | None:
    """Число сделок за 24 часа по свечам. None, если биржа их не публикует."""
    window = _day_window(history, candle_open_ms)
    if any(candle.trades is None for candle in window):
        return None
    return sum(candle.trades for candle in window)


def instrument_at(
    instrument: Instrument, history: list[Candle], candle_open_ms: int
) -> Instrument:
    """Инструмент таким, каким он выглядел на момент T.

    Оборот и число сделок пересчитываются по свечам. Статус торгов остаётся
    сегодняшним: исторического у нас нет, и это одно из смещений бэктеста,
    записанных в spec.md.
    """
    return replace(
        instrument,
        quote_volume_24h=turnover_24h(history, candle_open_ms),
        trades_24h=trades_24h(history, candle_open_ms),
    )


@dataclass
class BacktestStats:
    """Итоги бэктеста по одной бирже."""

    universe: int      # инструментов в выборке
    downloaded: int    # для скольких удалось загрузить историю
    runs: int          # записано прогонов
    candidates: int    # записано кандидатов
    outcomes: int      # записано результатов


def backtest_exchange(
    client,
    connection: sqlite3.Connection,
    moments: list[int],
    log=print,
) -> BacktestStats:
    """Проиграть заданные моменты как последовательность прогонов."""
    filters = DEFAULT.filters[client.NAME]

    instruments = client.get_instruments()
    universe, _ = apply_filters(instruments, filters)
    log(f"{client.NAME}: инструментов в выборке {len(universe)}")

    start_ms, end_ms = history_span(moments)
    histories: dict[str, list[Candle]] = {}
    for number, instrument in enumerate(universe, start=1):
        histories[instrument.symbol] = fetch_history(
            client, instrument.symbol, start_ms, end_ms
        )
        if number % 10 == 0:
            log(f"   загружено {number} из {len(universe)}")

    by_symbol = {instrument.symbol: instrument for instrument in universe}
    runs = candidates_written = outcomes_written = 0

    for moment in moments:
        windows = {}
        for symbol, history in histories.items():
            window = window_at(history, moment)
            if not window:
                continue
            if rejection_reason(instrument_at(by_symbol[symbol], history, moment), filters):
                continue
            windows[symbol] = window

        metrics_by_symbol = {}
        for symbol, window in windows.items():
            metrics = compute_metrics(window)
            if metrics is not None:
                metrics_by_symbol[symbol] = metrics

        candidates = select_candidates(metrics_by_symbol)
        total = sum(
            1 for metrics in metrics_by_symbol.values() if is_candidate(metrics)
        )

        run_id = save_run(
            connection,
            exchange=client.NAME,
            candle_open_ms=moment,
            total_symbols=len(instruments),
            passed_filters=len(windows),
            analysed_symbols=len(metrics_by_symbol),
            candidates_count=total,
            config=DEFAULT,
            source=SOURCE,
        )
        turnover = {
            symbol: turnover_24h(histories[symbol], moment) for symbol in windows
        }
        save_candidates(connection, run_id, client.NAME, candidates, turnover)

        runs += 1
        candidates_written += len(candidates)
        outcomes_written += _write_outcomes(
            connection, run_id, candidates, histories, moment
        )

    return BacktestStats(
        universe=len(universe),
        downloaded=len(histories),
        runs=runs,
        candidates=candidates_written,
        outcomes=outcomes_written,
    )


def _write_outcomes(
    connection: sqlite3.Connection,
    run_id: int,
    candidates: list,
    histories: dict[str, list[Candle]],
    moment: int,
) -> int:
    """Заполнить результаты кандидатов прямо сейчас.

    В бэктесте ждать восьми часов не нужно: «будущее» для этой свечи уже
    лежит в загруженной истории.
    """
    ids = candidate_ids(connection, run_id)
    written = 0

    for candidate in candidates:
        outcome = compute_outcome(
            candidate.metrics.close,
            moment,
            future_at(histories[candidate.symbol], moment),
        )
        if outcome.ret_8h is None:
            continue
        save_outcome(
            connection,
            ids[candidate.symbol],
            outcome.ret_30m,
            outcome.ret_2h,
            outcome.ret_8h,
            outcome.max_move_2h,
        )
        written += 1

    return written


def main() -> None:
    # Последний прогон отстоит от текущей свечи на горизонт outcomes:
    # для более поздних свечей будущего ещё не существует.
    boundary = last_closed_open_time(EXCHANGES[0].fetch_server_time())
    moments = run_times(boundary - FUTURE_CANDLES * INTERVAL_MS, DEFAULT_RUNS)

    print(f"Бэктест: {len(moments)} прогонов на биржу, шаг {INTERVAL}")
    print(f"Период: {to_iso(moments[0])} .. {to_iso(moments[-1])} UTC")
    print()

    connection = connect()
    try:
        for client in EXCHANGES:
            stats = backtest_exchange(client, connection, moments)
            print(
                f"{client.NAME}: прогонов {stats.runs}, "
                f"кандидатов {stats.candidates}, результатов {stats.outcomes}"
            )
            print()
    finally:
        connection.close()


if __name__ == "__main__":
    main()
