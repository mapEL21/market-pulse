"""Точка входа Market Pulse.

Запуск: python -m src.main

Реализованы этапы 1-5: список бессрочных USDT-контрактов Binance,
отсев неликвида, загрузка свечей, расчёт метрик RVOL / RTC / VE,
скоринг и отбор кандидатов. Отчёт с объяснениями — следующий этап.
"""

from datetime import datetime, timezone

from src.candles import (
    INTERVAL,
    LoadStats,
    last_closed_open_time,
    load_candles,
)
from src.config import DEFAULT, Config
from src.exchanges import binance
from src.filters import FilterStats, apply_filters
from src.metrics import compute_all
from src.scoring import Candidate, is_candidate, select_candidates


def format_amount(value: float) -> str:
    """6712171616 -> '6 712 171 616'.

    Разряды разделяются пробелом: в столбце из 527 строк группы цифр
    заметно легче сравнивать глазом, чем сплошное число.
    """
    return f"{value:,.0f}".replace(",", " ")


def reason_labels(config: Config = DEFAULT) -> dict[str, str]:
    """Подписи к причинам отсева.

    Числа в подписях подставляются из самого конфига, а не пишутся руками:
    иначе после правки порога отчёт продолжил бы показывать старое значение,
    и понять это по выводу было бы невозможно.
    """
    return {
        "status": f"статус торгов не {config.active_status}",
        "volume": f"оборот < {format_amount(config.min_quote_volume_24h)} USDT",
        "trades": f"сделок < {format_amount(config.min_trades_24h)}",
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


def print_candidates(
    candidates: list[Candidate], total: int, config: Config = DEFAULT
) -> None:
    """Таблица кандидатов по убыванию score."""
    print(
        f"Пороги интереса: RVOL >= {config.rvol_threshold}, "
        f"RTC >= {config.rtc_threshold}, VE >= {config.ve_threshold}; "
        f"кандидат — не меньше {config.min_triggered_metrics} из 3"
    )
    print(f"Кандидатов: {total}, показаны {len(candidates)} (топ-{config.top_n})")
    print()

    header = (
        f"{'#':>4}  {'SYMBOL':<14}{'SCORE':>7}{'RVOL':>9}{'RTC':>9}{'VE':>8}"
        f"{'CHG':>9}{'ОБЪЁМ СВЕЧИ':>16}{'ОБЫЧНО':>14}"
    )
    print(header)
    print("-" * len(header))

    for candidate in candidates:
        metrics = candidate.metrics
        print(
            f"{candidate.rank:>4}  {candidate.symbol:<14}{candidate.score:>7.2f}"
            f"{metrics.rvol:>8.2f}x{metrics.rtc:>8.2f}x{metrics.ve:>7.2f}x"
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

    total = sum(
        1 for metrics in metrics_by_symbol.values() if is_candidate(metrics)
    )
    print_candidates(select_candidates(metrics_by_symbol), total)


if __name__ == "__main__":
    main()
