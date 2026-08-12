"""Точка входа Market Pulse.

Запуск: python -m src.main

Реализованы этапы 1-4: список бессрочных USDT-контрактов Binance,
отсев неликвида, загрузка свечей и расчёт метрик RVOL / RTC / VE.
Скоринг и отчёт — следующие этапы.
"""

from datetime import datetime, timezone

from src.candles import (
    INTERVAL,
    LoadStats,
    last_closed_open_time,
    load_candles,
)
from src.exchanges import binance
from src.filters import (
    MIN_QUOTE_VOLUME_24H,
    MIN_TRADES_24H,
    ACTIVE_STATUS,
    FilterStats,
    apply_filters,
)
from src.metrics import Metrics, compute_all


def format_amount(value: float) -> str:
    """6712171616 -> '6 712 171 616'.

    Разряды разделяются пробелом: в столбце из 527 строк группы цифр
    заметно легче сравнивать глазом, чем сплошное число.
    """
    return f"{value:,.0f}".replace(",", " ")


def reason_labels() -> dict[str, str]:
    """Подписи к причинам отсева.

    Числа в подписях подставляются из самих порогов, а не пишутся руками:
    иначе после правки порога в filters.py отчёт продолжил бы показывать
    старое значение, и понять это по выводу было бы невозможно.
    """
    return {
        "status": f"статус торгов не {ACTIVE_STATUS}",
        "volume": f"оборот < {format_amount(MIN_QUOTE_VOLUME_24H)} USDT",
        "trades": f"сделок < {format_amount(MIN_TRADES_24H)}",
    }


def format_candle_time(open_time_ms: int) -> str:
    """1786497300000 -> '2026-08-12 01:15 UTC'."""
    moment = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def print_filter_stats(stats: FilterStats, last_closed_open: int) -> None:
    """Напечатать шапку прогона и сводку по отсеву."""
    print(
        f"BINANCE USDT-M perpetual — проверено {stats.total}, "
        f"после фильтров {stats.passed}"
    )
    print(f"свеча {INTERVAL}, открытие {format_candle_time(last_closed_open)}")
    print()
    print("Отсев:")

    labels = reason_labels()
    for reason, count in stats.rejected.items():
        print(f"  {labels[reason]:<28}{count:>5}")


def print_metrics(metrics_by_symbol: dict[str, Metrics]) -> None:
    """Таблица метрик, по убыванию RVOL.

    Сортировка по RVOL временная: на этапе 5 её заменит score, который
    учитывает все три метрики сразу.
    """
    header = (
        f"{'#':>4}  {'SYMBOL':<14}{'RVOL':>8}{'RTC':>8}{'VE':>8}{'CHG':>9}"
        f"{'ОБЪЁМ СВЕЧИ':>16}{'ОБЫЧНО':>14}"
    )
    print(header)
    print("-" * len(header))

    ranked = sorted(
        metrics_by_symbol.items(), key=lambda item: item[1].rvol, reverse=True
    )
    for number, (symbol, metrics) in enumerate(ranked, start=1):
        print(
            f"{number:>4}  {symbol:<14}"
            f"{metrics.rvol:>7.2f}x{metrics.rtc:>7.2f}x{metrics.ve:>7.2f}x"
            f"{metrics.change_pct:>8.2f}%"
            f"{format_amount(metrics.volume):>16}"
            f"{format_amount(metrics.volume_median):>14}"
        )


def print_load_stats(stats: LoadStats) -> None:
    """Напечатать итоги загрузки свечей."""
    print(
        f"Загрузка свечей: {stats.requested} инструментов за "
        f"{stats.elapsed_sec:.0f} с "
        f"(скачано {stats.from_api}, из кэша {stats.from_cache})"
    )
    print(f"Отсев по короткой истории: {stats.too_short}")
    print(f"Готово к расчёту метрик: {stats.loaded}")
    print(f"Израсходовано веса за минуту: {stats.used_weight}")


def main() -> None:
    last_closed_open = last_closed_open_time(binance.fetch_server_time())

    instruments = binance.get_instruments()
    passed, filter_stats = apply_filters(instruments)

    print_filter_stats(filter_stats, last_closed_open)

    windows, load_stats = load_candles(passed, last_closed_open)
    print_load_stats(load_stats)

    metrics_by_symbol, skipped = compute_all(windows)
    print(f"Метрика не посчитана (нулевая медиана): {len(skipped)}")
    print()

    print_metrics(metrics_by_symbol)


if __name__ == "__main__":
    main()
