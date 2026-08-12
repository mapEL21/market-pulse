"""Точка входа Market Pulse.

Запуск: python -m src.main

Реализован этап 1: список бессрочных USDT-контрактов Binance
с 24-часовой статистикой. Фильтры, метрики и отчёт — следующие этапы.
"""

from src.exchanges import binance
from src.exchanges.binance import Instrument


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


def print_instruments(instruments: list[Instrument]) -> None:
    """Напечатать таблицу инструментов целиком."""
    print(f"BINANCE USDT-M perpetual — инструментов: {len(instruments)}")
    print("24-часовой тикер, оборот в USDT, сортировка по убыванию оборота.")
    print()

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
    print_instruments(binance.get_instruments())


if __name__ == "__main__":
    main()
