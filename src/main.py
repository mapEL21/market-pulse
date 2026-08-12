"""Точка входа Market Pulse.

Запуск: python -m src.main

Реализованы этапы 1-2: список бессрочных USDT-контрактов Binance
с 24-часовой статистикой и отсев неликвида. Метрики, скоринг и отчёт —
следующие этапы.
"""

from src.exchanges import binance
from src.exchanges.binance import Instrument
from src.filters import (
    MIN_QUOTE_VOLUME_24H,
    MIN_TRADES_24H,
    ACTIVE_STATUS,
    FilterStats,
    apply_filters,
)


def format_amount(value: float) -> str:
    """6712171616 -> '6 712 171 616'.

    Разряды разделяются пробелом: в столбце из 527 строк группы цифр
    заметно легче сравнивать глазом, чем сплошное число.
    """
    return f"{value:,.0f}".replace(",", " ")


def format_price(value: float) -> str:
    """Цена с 8 значащими цифрами.

    Цены различаются на порядки — 63758.1 и 0.007377 одновременно, — поэтому
    фиксированное число знаков после запятой не подходит: оно либо обрежет
    дешёвые инструменты до нуля, либо забьёт таблицу нулями у дорогих.
    """
    return f"{value:,.8g}".replace(",", " ")


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


def print_filter_stats(stats: FilterStats) -> None:
    """Напечатать сводку по отсеву."""
    print(
        f"BINANCE USDT-M perpetual — проверено {stats.total}, "
        f"после фильтров {stats.passed}"
    )
    print("24-часовой тикер, оборот в USDT, сортировка по убыванию оборота.")
    print()
    print("Отсев:")

    labels = reason_labels()
    for reason, count in stats.rejected.items():
        print(f"  {labels[reason]:<28}{count:>5}")


def print_instruments(instruments: list[Instrument]) -> None:
    """Напечатать таблицу инструментов целиком."""
    header = (
        f"{'#':>4}  {'SYMBOL':<14}{'ОБОРОТ 24Ч':>18}"
        f"{'СДЕЛОК 24Ч':>14}{'ЦЕНА':>16}{'ИЗМ. 24Ч':>11}"
    )
    print(header)
    print("-" * len(header))

    for number, instrument in enumerate(instruments, start=1):
        print(
            f"{number:>4}  {instrument.symbol:<14}"
            f"{format_amount(instrument.quote_volume_24h):>18}"
            f"{format_amount(instrument.trades_24h):>14}"
            f"{format_price(instrument.last_price):>16}"
            f"{instrument.change_pct_24h:>10.2f}%"
        )


def main() -> None:
    instruments = binance.get_instruments()
    passed, stats = apply_filters(instruments)

    print_filter_stats(stats)
    print()
    print_instruments(passed)


if __name__ == "__main__":
    main()
