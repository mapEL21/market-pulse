"""Отсев инструментов, которые не имеет смысла анализировать.

Пороги — раздел 5 spec.md, их значения лежат в config.py. Фильтры применяются
до загрузки свечей: их задача в том, чтобы сократить число инструментов, для
которых придётся качать историю. Поэтому здесь используются только данные
24-часового тикера, полученные одним запросом на этапе 1.

Фильтр «котируемая валюта не USDT» здесь отсутствует: отбор USDT-перпетуалов
зашит в fetch_symbols, до этого модуля другие инструменты не доходят.

Фильтр «длина истории меньше окна» живёт в candles.py: проверить его можно
только по факту загруженной истории.
"""

from dataclasses import dataclass

from src.config import BINANCE_FILTERS, ExchangeFilters
from src.exchanges.base import Instrument

# Порядок проверок и порядок строк в отчёте об отсеве.
REASONS = ("status", "volume", "trades")


@dataclass
class FilterStats:
    """Сколько инструментов было, сколько прошло и что кого отсеяло."""

    total: int
    passed: int
    rejected: dict[str, int]  # код причины -> сколько инструментов


def rejection_reason(
    instrument: Instrument, filters: ExchangeFilters = BINANCE_FILTERS
) -> str | None:
    """Код первой сработавшей причины отсева или None, если инструмент прошёл.

    Порядок проверок идёт от общего к частному: инструмент, снятый с торгов,
    учитывается как снятый с торгов, а не как неликвидный — иначе статистика
    отсева отвечала бы не на тот вопрос, который задан.

    Фильтр по числу сделок пропускается, если биржа этих данных не отдаёт:
    у OKX их нет ни в свечах, ни в тикере.
    """
    if instrument.status != filters.active_status:
        return "status"
    if instrument.quote_volume_24h < filters.min_quote_volume_24h:
        return "volume"
    if filters.min_trades_24h is not None and instrument.trades_24h is not None:
        if instrument.trades_24h < filters.min_trades_24h:
            return "trades"
    return None


def apply_filters(
    instruments: list[Instrument], filters: ExchangeFilters = BINANCE_FILTERS
) -> tuple[list[Instrument], FilterStats]:
    """Разделить список на прошедших фильтры и статистику отсева.

    Каждый инструмент попадает ровно в одну строку статистики, поэтому суммы
    причин и числа прошедших в сумме дают исходное количество.
    """
    passed: list[Instrument] = []
    rejected = {reason: 0 for reason in REASONS}

    for instrument in instruments:
        reason = rejection_reason(instrument, filters)
        if reason is None:
            passed.append(instrument)
        else:
            rejected[reason] += 1

    stats = FilterStats(total=len(instruments), passed=len(passed), rejected=rejected)
    return passed, stats
