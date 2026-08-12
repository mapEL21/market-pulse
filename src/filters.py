"""Отсев инструментов, которые не имеет смысла анализировать.

Пороги — раздел 5 spec.md. Фильтры применяются до загрузки свечей: их задача
в том, чтобы сократить число инструментов, для которых придётся качать по
193 свечи. Поэтому здесь используются только данные 24-часового тикера,
которые уже получены одним запросом на этапе 1.

Фильтр «котируемая валюта не USDT» здесь отсутствует: отбор USDT-перпетуалов
зашит в fetch_symbols, до этого модуля другие инструменты не доходят.

Фильтр «длина истории меньше N+1 свечей» будет на этапе 3, вместе со свечами:
проверить его можно только по факту загруженной истории.
"""

from dataclasses import dataclass

from src.exchanges.binance import Instrument

ACTIVE_STATUS = "TRADING"
MIN_QUOTE_VOLUME_24H = 5_000_000  # USDT за 24 часа
MIN_TRADES_24H = 10_000  # сделок за 24 часа

# Порядок проверок и порядок строк в отчёте об отсеве.
REASONS = ("status", "volume", "trades")


@dataclass
class FilterStats:
    """Сколько инструментов было, сколько прошло и что кого отсеяло."""

    total: int
    passed: int
    rejected: dict[str, int]  # код причины -> сколько инструментов


def rejection_reason(instrument: Instrument) -> str | None:
    """Код первой сработавшей причины отсева или None, если инструмент прошёл.

    Порядок проверок идёт от общего к частному: инструмент, снятый с торгов,
    учитывается как снятый с торгов, а не как неликвидный — иначе статистика
    отсева отвечала бы не на тот вопрос, который задан.
    """
    if instrument.status != ACTIVE_STATUS:
        return "status"
    if instrument.quote_volume_24h < MIN_QUOTE_VOLUME_24H:
        return "volume"
    if instrument.trades_24h < MIN_TRADES_24H:
        return "trades"
    return None


def apply_filters(
    instruments: list[Instrument],
) -> tuple[list[Instrument], FilterStats]:
    """Разделить список на прошедших фильтры и статистику отсева.

    Каждый инструмент попадает ровно в одну строку статистики, поэтому суммы
    причин и числа прошедших в сумме дают исходное количество.
    """
    passed: list[Instrument] = []
    rejected = {reason: 0 for reason in REASONS}

    for instrument in instruments:
        reason = rejection_reason(instrument)
        if reason is None:
            passed.append(instrument)
        else:
            rejected[reason] += 1

    stats = FilterStats(total=len(instruments), passed=len(passed), rejected=rejected)
    return passed, stats
