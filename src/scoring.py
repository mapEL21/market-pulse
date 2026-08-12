"""Скоринг и отбор кандидатов. Формулы — раздел 7 spec.md.

Модуль ничего не знает про биржи, сеть и вывод: на входе метрики, на выходе
упорядоченный список кандидатов.
"""

from dataclasses import dataclass

from src.config import DEFAULT, Config
from src.metrics import Metrics


@dataclass
class Candidate:
    """Инструмент, отобранный для ручного просмотра."""

    symbol: str
    rank: int
    score: float
    metrics: Metrics


def normalized(value: float, threshold: float, cap: float) -> float:
    """Метрика в единицах своего порога, ограниченная сверху.

    Ровно на пороге получается 1.0 — это делает три разные метрики
    сравнимыми между собой, хотя пороги у них разные (3.0, 2.5 и 2.0).

    Потолок нужен, чтобы один экстремальный выброс не перекрывал вклад
    двух остальных метрик. Следствие: при cap = 3.0 значение RVOL = 9
    и значение RVOL = 300 дают одинаковый вклад в score.
    """
    return min(value / threshold, cap)


def score(metrics: Metrics, config: Config = DEFAULT) -> float:
    """Взвешенная сумма нормированных метрик.

    Веса в сумме дают 1.0, поэтому инструмент, у которого все три метрики
    ровно на порогах, получает score ровно 1.0.
    """
    return (
        config.rvol_weight
        * normalized(metrics.rvol, config.rvol_threshold, config.score_cap)
        + config.rtc_weight
        * normalized(metrics.rtc, config.rtc_threshold, config.score_cap)
        + config.ve_weight
        * normalized(metrics.ve, config.ve_threshold, config.score_cap)
    )


def triggered_metrics(metrics: Metrics, config: Config = DEFAULT) -> int:
    """Сколько метрик из трёх достигли своего порога.

    Неравенство нестрогое: раздел 6 spec.md задаёт пороги как «RVOL >= 3.0».
    Это противоположно фильтрам этапа 2, где неравенства строгие.
    """
    return sum(
        (
            metrics.rvol >= config.rvol_threshold,
            metrics.rtc >= config.rtc_threshold,
            metrics.ve >= config.ve_threshold,
        )
    )


def is_candidate(metrics: Metrics, config: Config = DEFAULT) -> bool:
    """Прошёл ли инструмент правило «хотя бы две метрики из трёх».

    Защита от разовой аномалии в одном показателе: всплеск объёма без роста
    числа сделок и без расширения диапазона чаще всего означает одну крупную
    сделку, а не смену режима инструмента.
    """
    return triggered_metrics(metrics, config) >= config.min_triggered_metrics


def select_candidates(
    metrics_by_symbol: dict[str, Metrics], config: Config = DEFAULT
) -> list[Candidate]:
    """Кандидаты по убыванию score, не больше config.top_n штук.

    Порядок задаётся тройкой ключей: score по убыванию, RVOL по убыванию,
    символ по возрастанию.

    Второй и третий ключи нужны из-за потолка нормировки. Инструмент, у
    которого все три метрики упёрлись в потолок, получает ровно score_cap,
    и таких в одном прогоне бывает несколько — различить их по score нельзя.
    Без явных дополнительных ключей порядок определялся бы порядком словаря,
    то есть оборотом за 24 часа, и два прогона на одних и тех же данных могли
    бы выдать разные списки — сравнивать прогоны в базе стало бы нельзя.

    RVOL взят вторым ключом, а не символом: среди упёршихся в потолок вперёд
    должен идти тот, у кого сильнее исходный сигнал, иначе позиция в списке
    вводит в заблуждение. Символ остаётся последним ключом на случай полного
    совпадения и гарантирует воспроизводимость.
    """
    scored = [
        (symbol, score(metrics, config), metrics)
        for symbol, metrics in metrics_by_symbol.items()
        if is_candidate(metrics, config)
    ]
    scored.sort(key=lambda item: (-item[1], -item[2].rvol, item[0]))

    return [
        Candidate(symbol=symbol, rank=rank, score=value, metrics=metrics)
        for rank, (symbol, value, metrics) in enumerate(
            scored[: config.top_n], start=1
        )
    ]
