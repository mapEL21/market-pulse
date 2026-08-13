"""Скоринг и отбор кандидатов. Формулы — раздел 7 spec.md.

Модуль ничего не знает про биржи, сеть и вывод: на входе метрики, на выходе
упорядоченный список кандидатов.
"""

from dataclasses import dataclass

from src.config import DEFAULT, Config
from src.metrics import Metrics


@dataclass
class Observation:
    """Инструмент, дошедший до расчёта метрик в одном прогоне.

    Наблюдением становится каждый инструмент, а не только попавший в отчёт.
    Иначе на вопросы вроде «а если бы порог RVOL был 5.0» ответить нечем:
    инструментов ниже текущего порога в данных просто не окажется.

    rank заполнен только у кандидатов — тех, кто прошёл правило «не меньше
    двух метрик» и попал в топ-N. У остальных None.
    """

    symbol: str
    score: float
    triggered: int      # сколько метрик достигли порога
    metrics: Metrics
    rank: int | None = None

    @property
    def is_candidate(self) -> bool:
        return self.rank is not None


def normalized(value: float, threshold: float, cap: float) -> float:
    """Метрика в единицах своего порога, ограниченная сверху.

    Ровно на пороге получается 1.0 — это делает три разные метрики
    сравнимыми между собой, хотя пороги у них разные (3.0, 2.5 и 2.0).

    Потолок нужен, чтобы один экстремальный выброс не перекрывал вклад
    двух остальных метрик. Следствие: при cap = 3.0 значение RVOL = 9
    и значение RVOL = 300 дают одинаковый вклад в score.
    """
    return min(value / threshold, cap)


def _weighted_components(
    metrics: Metrics, config: Config
) -> list[tuple[float, float]]:
    """Пары (вес, нормированное значение) по метрикам, которые удалось посчитать."""
    components = [
        (
            config.rvol_weight,
            normalized(metrics.rvol, config.rvol_threshold, config.score_cap),
        ),
        (
            config.ve_weight,
            normalized(metrics.ve, config.ve_threshold, config.score_cap),
        ),
    ]
    if metrics.rtc is not None:
        components.append(
            (
                config.rtc_weight,
                normalized(metrics.rtc, config.rtc_threshold, config.score_cap),
            )
        )
    return components


def score(metrics: Metrics, config: Config = DEFAULT) -> float:
    """Средневзвешенное нормированных метрик.

    Деление на сумму весов существенно, когда метрик не три, а две: у OKX нет
    числа сделок, и без нормировки максимум score там был бы 0.7 вместо 1.0,
    а инструмент ровно на порогах получал бы 0.7. Шкала перестала бы значить
    одно и то же на разных биржах.

    Когда доступны все три метрики, сумма весов равна единице, и формула
    сводится к записанной в разделе 7 spec.md.
    """
    components = _weighted_components(metrics, config)
    total_weight = sum(weight for weight, _ in components)
    return sum(weight * value for weight, value in components) / total_weight


def available_metrics(metrics: Metrics) -> int:
    """Сколько метрик вообще удалось посчитать для этого инструмента."""
    return 3 if metrics.rtc is not None else 2


def triggered_metrics(metrics: Metrics, config: Config = DEFAULT) -> int:
    """Сколько метрик достигли своего порога.

    Неравенство нестрогое: раздел 6 spec.md задаёт пороги как «RVOL >= 3.0».
    Это противоположно фильтрам этапа 2, где неравенства строгие.
    """
    triggered = [
        metrics.rvol >= config.rvol_threshold,
        metrics.ve >= config.ve_threshold,
    ]
    if metrics.rtc is not None:
        triggered.append(metrics.rtc >= config.rtc_threshold)
    return sum(triggered)


def is_candidate(metrics: Metrics, config: Config = DEFAULT) -> bool:
    """Прошёл ли инструмент правило «хотя бы две метрики».

    Защита от разовой аномалии в одном показателе: всплеск объёма без роста
    числа сделок и без расширения диапазона чаще всего означает одну крупную
    сделку, а не смену режима инструмента.

    Отдельного случая для двух доступных метрик не нужно: правило «не меньше
    двух» при двух метриках само превращается в «обе», что как раз и требуется.
    """
    return triggered_metrics(metrics, config) >= config.min_triggered_metrics


def observe(
    metrics_by_symbol: dict[str, Metrics], config: Config = DEFAULT
) -> list[Observation]:
    """Все наблюдения прогона, по убыванию score; ранг только у кандидатов.

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
    observations = [
        Observation(
            symbol=symbol,
            score=score(metrics, config),
            triggered=triggered_metrics(metrics, config),
            metrics=metrics,
        )
        for symbol, metrics in metrics_by_symbol.items()
    ]
    observations.sort(
        key=lambda item: (-item.score, -item.metrics.rvol, item.symbol)
    )

    rank = 0
    for observation in observations:
        if rank >= config.top_n:
            break
        if observation.triggered >= config.min_triggered_metrics:
            rank += 1
            observation.rank = rank

    return observations


def select_candidates(
    metrics_by_symbol: dict[str, Metrics], config: Config = DEFAULT
) -> list[Observation]:
    """Только кандидаты — те, у кого есть ранг."""
    return [
        observation
        for observation in observe(metrics_by_symbol, config)
        if observation.is_candidate
    ]
