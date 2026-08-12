"""Точка входа Market Pulse.

Запуск: python -m src.main

Реализованы этапы 1-8: Binance и OKX, отсев неликвида, загрузка свечей,
расчёт метрик, скоринг, отчёт с объяснениями и сохранение прогонов в SQLite.

Модуль только связывает шаги и печатает готовые строки. Всё форматирование
живёт в report.py, все вычисления — в остальных модулях.
"""

import sqlite3

from src.candles import last_closed_open_time, load_candles
from src.config import DEFAULT
from src.exchanges import binance, okx
from src.filters import apply_filters
from src.metrics import compute_all
from src.report import (
    render_candidate,
    render_filter_stats,
    render_funnel,
    render_header,
    render_load_stats,
)
from src.scoring import is_candidate, select_candidates
from src.storage import connect, run_exists, save_candidates, save_run, to_iso

EXCHANGES = (binance, okx)


def analyse(client, last_closed_open: int, connection: sqlite3.Connection) -> None:
    """Полный проход по одной бирже: отбор, метрики, отчёт, запись в базу."""
    filters = DEFAULT.filters[client.NAME]

    instruments = client.get_instruments()
    passed, filter_stats = apply_filters(instruments, filters)
    print(render_filter_stats(filter_stats, filters))

    windows, load_stats = load_candles(client, passed, last_closed_open)
    print(render_load_stats(load_stats))

    metrics_by_symbol, skipped = compute_all(windows)
    if skipped:
        print(f"Метрика не посчитана (нулевая медиана): {len(skipped)}")

    # Кандидатов может быть больше, чем помещается в выдачу: select_candidates
    # усекает список до top_n, а в воронку и в базу должно попасть полное число.
    candidates_total = sum(
        1 for metrics in metrics_by_symbol.values() if is_candidate(metrics)
    )
    candidates = select_candidates(metrics_by_symbol)

    print()
    print(
        render_funnel(
            exchange=f"{client.NAME} {client.MARKET}",
            total=filter_stats.total,
            passed=filter_stats.passed,
            analysed=len(metrics_by_symbol),
            candidates=candidates_total,
            shown=len(candidates),
        )
    )

    turnover = {
        instrument.symbol: instrument.quote_volume_24h for instrument in instruments
    }
    for candidate in candidates:
        print()
        print(render_candidate(candidate, turnover[candidate.symbol]))

    print()
    print(
        store_run(
            connection=connection,
            exchange=client.NAME,
            last_closed_open=last_closed_open,
            total_symbols=filter_stats.total,
            passed_filters=filter_stats.passed,
            analysed_symbols=len(metrics_by_symbol),
            candidates_total=candidates_total,
            candidates=candidates,
            turnover=turnover,
        )
    )


def store_run(
    connection: sqlite3.Connection,
    exchange: str,
    last_closed_open: int,
    total_symbols: int,
    passed_filters: int,
    analysed_symbols: int,
    candidates_total: int,
    candidates: list,
    turnover: dict[str, float],
) -> str:
    """Сохранить прогон одной биржи и вернуть строку для вывода."""
    duplicate = run_exists(connection, exchange, to_iso(last_closed_open))

    run_id = save_run(
        connection,
        exchange=exchange,
        candle_open_ms=last_closed_open,
        total_symbols=total_symbols,
        passed_filters=passed_filters,
        analysed_symbols=analysed_symbols,
        candidates_count=candidates_total,
        config=DEFAULT,
    )
    save_candidates(connection, run_id, exchange, candidates, turnover)

    warning = (
        "\nВнимание: для этой свечи прогон уже был. Запись добавлена, "
        "но при анализе такие дубли надо учитывать."
        if duplicate
        else ""
    )
    return f"Прогон сохранён в базу: runs.id = {run_id}{warning}"


def main() -> None:
    # Граница считается один раз и по времени первой биржи: свечи 15m у бирж
    # совпадают по стенным часам, и оба списка должны относиться к одной свече.
    last_closed_open = last_closed_open_time(EXCHANGES[0].fetch_server_time())
    print(render_header(last_closed_open))

    connection = connect()
    try:
        for client in EXCHANGES:
            print()
            analyse(client, last_closed_open, connection)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
