"""Объяснение, почему инструмент попал в список.

Раздел 8 spec.md: пользователь должен видеть и коэффициент, и абсолютные
числа, из которых он получен. Голый «score 2.31» без обоснования неприемлем.

Объяснение строится как данные — список Reason, — а не сразу как готовый
текст. Тогда другой формат вывода сможет отрисовать те же самые причины,
и формулировки не разойдутся между форматами по построению.
"""

import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone

from src.candles import (
    BASELINE_CANDLES,
    BASELINE_HOURS,
    INTERVAL,
    INTERVAL_MS,
    LoadStats,
)
from src.config import BINANCE_FILTERS, DEFAULT, Config, ExchangeFilters
from src.filters import FilterStats
from src.metrics import Metrics
from src.scoring import Observation

LINE_WIDTH = 78

# Насколько средний размер сделки должен отличаться от обычного, чтобы об этом
# стоило писать отдельной фразой. Порог оформительский: он не влияет ни на
# отбор кандидатов, ни на ранжирование, поэтому в config.py ему не место.
ATS_NOTABLE = 1.3


@dataclass
class Reason:
    """Одна строка объяснения — одна метрика."""

    what: str        # "Объём свечи 7.3M USDT"
    ratio: str       # "в 236.8× выше обычного"
    usual: str       # "обычно 30.6K USDT за свечу"
    triggered: bool  # достигнут ли порог интереса
    threshold: str   # "порог 3.0×" — показывается только у несработавших


def compact(value: float) -> str:
    """7250809 -> '7.3M', 30622 -> '30.6K', 862 -> '862'.

    Обороты в отчёте занимают место в строке рядом с текстом, и полное число
    с разрядами мешает читать. Для счётных величин вроде числа сделок такая
    запись не годится — там нужны единицы, поэтому есть отдельная функция.
    """
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= limit:
            return f"{value / limit:.1f}{suffix}"
    return f"{value:.0f}"


def format_amount(value: float) -> str:
    """31400 -> '31 400'. Разряды разделяются пробелом."""
    return f"{value:,.0f}".replace(",", " ")


def format_symbol(symbol: str) -> str:
    """COTIUSDT -> 'COTIUSDT · PERP', COTI-USDT-SWAP -> без изменений.

    Тикер печатается ровно так, как он называется на бирже, без слэша:
    запись вида COTI/USDT привычно означает спотовую пару, а анализируются
    только бессрочные фьючерсы. Метка PERP убирает эту двусмысленность —
    у одного и того же актива спот и перпетуал живут своей жизнью, и путать
    их при переходе к графику нельзя.

    Тикерам OKX метка не нужна: в них уже есть SWAP.
    """
    if symbol.endswith("-SWAP"):
        return symbol
    return f"{symbol} · PERP"


def format_candle_close(open_time_ms: int) -> str:
    """Время закрытия свечи, открывшейся в указанный момент.

    В отчёте свеча называется по концу — так задано в разделе 8 spec.md.
    Внутри программы та же свеча всюду обозначается временем открытия,
    потому что именно оно приходит от биржи.
    """
    closed_at = datetime.fromtimestamp(
        (open_time_ms + INTERVAL_MS) / 1000, tz=timezone.utc
    )
    return closed_at.strftime("%Y-%m-%d %H:%M UTC")


def ats(metrics: Metrics) -> float | None:
    """Во сколько раз изменился средний размер сделки (раздел 6.5 spec.md).

    Тождественно равно RVOL / RTC. None в двух случаях: биржа не публикует
    число сделок (тогда RTC не посчитан) или сделок в свече не было вовсе.
    """
    if metrics.rtc is None or metrics.rtc == 0:
        return None
    return metrics.rvol / metrics.rtc


def build_reasons(metrics: Metrics, config: Config = DEFAULT) -> list[Reason]:
    """Причины — по одной на посчитанную метрику, в порядке разделов 6.1-6.3.

    Возвращаются и несработавшие: то, что диапазон свечи остался обычным, —
    тоже содержательная информация. Разделять их на блоки в отчёте будет
    рендер, по полю triggered.

    Причины по числу сделок нет вовсе, если биржа этих данных не публикует.
    Показать её с прочерком было бы хуже: строка выглядела бы как результат
    измерения, хотя измерения не было.
    """
    reasons = [
        Reason(
            what=f"Объём свечи {compact(metrics.volume)} USDT",
            ratio=f"в {metrics.rvol:.1f}× выше обычного",
            usual=f"обычно {compact(metrics.volume_median)} USDT за свечу",
            triggered=metrics.rvol >= config.rvol_threshold,
            threshold=f"порог {config.rvol_threshold}×",
        ),
    ]

    if metrics.rtc is not None:
        reasons.append(
            Reason(
                what=f"Сделок {format_amount(metrics.trades)}",
                ratio=f"в {metrics.rtc:.1f}× больше обычного",
                usual=f"обычно {format_amount(metrics.trades_median)} за свечу",
                triggered=metrics.rtc >= config.rtc_threshold,
                threshold=f"порог {config.rtc_threshold}×",
            )
        )

    reasons.append(
        Reason(
            what=f"Диапазон свечи {metrics.range_pct:.1f} %",
            ratio=f"в {metrics.ve:.1f}× шире обычного",
            usual=f"обычно {metrics.range_pct_median:.1f} %",
            triggered=metrics.ve >= config.ve_threshold,
            threshold=f"порог {config.ve_threshold}×",
        )
    )
    return reasons


def _sentence_without_trade_counts(volume: bool, spread: bool) -> str:
    """Фраза для биржи, которая не публикует число сделок.

    Кандидатом здесь можно стать только с обеими метриками: доступных две,
    а требуется не меньше двух. Поэтому случай ровно один.
    """
    if volume and spread:
        return (
            "Выше порога и объём, и диапазон свечи — инструмент вышел "
            "из своего обычного режима. Число сделок биржа не публикует, "
            "поэтому RTC и средний размер сделки не считались."
        )
    return (
        "Число сделок биржа не публикует: посчитаны только объём "
        "и диапазон свечи."
    )


def _pattern_sentence(volume: bool, trades: bool, spread: bool) -> str:
    """Фраза по набору сработавших метрик.

    Четыре набора, потому что кандидатом становится инструмент, у которого
    сработало не меньше двух метрик из трёх.

    Формулировки намеренно описательные и не называют несработавшую метрику
    «обычной». Метрика может не дойти до порога совсем чуть-чуть — в первом
    же прогоне попался инструмент с оборотом в 2.9× выше обычного при пороге
    3.0×, — и фраза «оборот остался обычным» была бы прямой неправдой.

    Содержательное различение «крупные участники или толпа мелких» даёт
    следующая фраза, через ATS: там оно опирается на конкретное число,
    а не на то, какие метрики пересекли порог.
    """
    if volume and trades and spread:
        return (
            "Все три метрики выше порога — инструмент вышел "
            "из своего обычного режима."
        )
    if volume and trades:
        return (
            "Выше порога объём и число сделок, диапазон свечи — нет: "
            "активность выросла без заметного расширения цены."
        )
    if volume and spread:
        return "Выше порога объём и диапазон свечи, число сделок — нет."
    return "Выше порога число сделок и диапазон свечи, оборот — нет."


def summary(metrics: Metrics, config: Config = DEFAULT) -> str:
    """Итоговая фраза: что показал набор метрик и что стало с размером сделки."""
    flags = [reason.triggered for reason in build_reasons(metrics, config)]

    if metrics.rtc is None:
        volume, spread = flags
        sentences = [_sentence_without_trade_counts(volume, spread)]
    else:
        volume, trades, spread = flags
        sentences = [_pattern_sentence(volume, trades, spread)]

    size = ats(metrics)
    if size is None:
        return " ".join(sentences)

    if size >= ATS_NOTABLE:
        sentences.append(f"Средняя сделка в {size:.1f}× крупнее обычной.")
    elif size <= 1 / ATS_NOTABLE:
        sentences.append(f"Средняя сделка в {1 / size:.1f}× мельче обычной.")
    else:
        sentences.append("Средний размер сделки почти не изменился.")

    return " ".join(sentences)


def render_header(last_closed_open: int) -> str:
    """Шапка прогона по разделу 8 spec.md."""
    return (
        f"Market Pulse — свеча {INTERVAL}, "
        f"закрытие {format_candle_close(last_closed_open)}\n"
        f"База сравнения: {BASELINE_CANDLES} свечи ({BASELINE_HOURS} ч), медиана"
    )


def reason_labels(filters: ExchangeFilters = BINANCE_FILTERS) -> dict[str, str]:
    """Подписи к причинам отсева.

    Числа подставляются из конфига, а не пишутся руками: иначе после правки
    порога отчёт продолжил бы показывать старое значение, и заметить это
    по выводу было бы невозможно.
    """
    trades = (
        "биржа не отдаёт число сделок"
        if filters.min_trades_24h is None
        else f"сделок < {format_amount(filters.min_trades_24h)}"
    )
    return {
        "status": f"статус торгов не {filters.active_status}",
        "volume": f"оборот < {compact(filters.min_quote_volume_24h)} USDT",
        "trades": trades,
    }


def render_filter_stats(
    stats: FilterStats, filters: ExchangeFilters = BINANCE_FILTERS
) -> str:
    """Сводка по отсеву на этапе фильтров."""
    labels = reason_labels(filters)
    lines = ["Отсев:"]
    lines += [
        f"  {labels[reason]:<28}{count:>5}" for reason, count in stats.rejected.items()
    ]
    return "\n".join(lines)


def render_load_stats(stats: LoadStats) -> str:
    """Итоги загрузки свечей."""
    return "\n".join(
        (
            f"Загрузка свечей: {stats.requested} инструментов за "
            f"{stats.elapsed_sec:.0f} с "
            f"(скачано {stats.from_api}, из кэша {stats.from_cache})",
            f"Отсев по короткой истории: {stats.too_short}",
            f"Израсходовано веса за минуту: {stats.used_weight}",
        )
    )


def render_funnel(
    exchange: str,
    total: int,
    passed: int,
    analysed: int,
    candidates: int,
    shown: int,
) -> str:
    """Строка воронки: сколько инструментов осталось после каждого шага.

    Число кандидатов и число показанных различаются, когда список усечён
    размером выдачи. Показывать вместо первого второе — значит скрывать,
    что часть кандидатов не попала в отчёт.
    """
    truncated = f" (показаны {shown})" if shown < candidates else ""
    return (
        f"{exchange}   проверено {total} → после фильтров {passed} → "
        f"с полной историей {analysed} → кандидатов {candidates}{truncated}"
    )


def render_candidate(
    candidate: Observation, volume_24h: float, config: Config = DEFAULT
) -> str:
    """Карточка одного кандидата по образцу из раздела 8 spec.md."""
    metrics = candidate.metrics
    rule = "─" * LINE_WIDTH

    lines = [
        rule,
        f"{candidate.rank:>2}. {format_symbol(candidate.symbol):<24}"
        f"score {candidate.score:.2f}",
        rule,
        f"    Изменение за свечу:  {metrics.change_pct:+.2f} %",
        f"    Оборот за 24 ч:      {compact(volume_24h)} USDT",
        "",
    ]

    reasons = build_reasons(metrics, config)
    triggered = [reason for reason in reasons if reason.triggered]
    missed = [reason for reason in reasons if not reason.triggered]

    lines.append("    Почему в списке:")
    for reason in triggered:
        lines.append(f"    • {reason.what} — {reason.ratio}")
        lines.append(f"      ({reason.usual})")

    if missed:
        lines.append("")
        lines.append("    Не сработало:")
        for reason in missed:
            lines.append(f"    • {reason.what} — {reason.ratio}")
            lines.append(f"      ({reason.usual}, {reason.threshold})")

    lines.append("")
    lines.append(
        textwrap.fill(
            summary(metrics, config),
            width=LINE_WIDTH,
            initial_indent="    Итог: ",
            subsequent_indent="    ",
        )
    )
    return "\n".join(lines)
