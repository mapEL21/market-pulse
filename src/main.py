"""Точка входа Market Pulse.

Запуск: python -m src.main

Реализованы этапы 1-6: список бессрочных USDT-контрактов Binance,
отсев неликвида, загрузка свечей, расчёт метрик RVOL / RTC / VE,
скоринг и отчёт с объяснениями.

Модуль только связывает шаги и печатает готовые строки. Всё форматирование
живёт в report.py, все вычисления — в остальных модулях.
"""

from src.candles import last_closed_open_time, load_candles
from src.exchanges import binance
from src.filters import apply_filters
from src.metrics import compute_all
from src.report import (
    render_candidate,
    render_filter_stats,
    render_funnel,
    render_header,
    render_load_stats,
)
from src.scoring import select_candidates


def main() -> None:
    last_closed_open = last_closed_open_time(binance.fetch_server_time())
    print(render_header(last_closed_open))
    print()

    instruments = binance.get_instruments()
    passed, filter_stats = apply_filters(instruments)
    print(render_filter_stats(filter_stats))

    windows, load_stats = load_candles(passed, last_closed_open)
    print(render_load_stats(load_stats))

    metrics_by_symbol, skipped = compute_all(windows)
    if skipped:
        print(f"Метрика не посчитана (нулевая медиана): {len(skipped)}")

    candidates = select_candidates(metrics_by_symbol)
    print()
    print(
        render_funnel(
            exchange="BINANCE",
            total=filter_stats.total,
            passed=filter_stats.passed,
            analysed=len(metrics_by_symbol),
            candidates=len(candidates),
        )
    )

    turnover = {
        instrument.symbol: instrument.quote_volume_24h for instrument in instruments
    }
    for candidate in candidates:
        print()
        print(render_candidate(candidate, turnover[candidate.symbol]))


if __name__ == "__main__":
    main()
