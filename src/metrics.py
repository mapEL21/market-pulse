"""Расчёт метрик активности. Формулы — раздел 6 spec.md.

Все три метрики устроены одинаково: значение последней закрытой свечи делится
на медиану того же значения по базовому окну. Медиана, а не среднее, — чтобы
один памп внутри окна не ослеплял метрику (раздел 4 spec.md).

Модуль ничего не знает ни про сеть, ни про биржи, ни про вывод: на входе
список свечей, на выходе числа.
"""

from dataclasses import dataclass
from statistics import median

from src.candles import WINDOW_CANDLES
from src.exchanges.binance import Candle


@dataclass
class Metrics:
    """Метрики одного инструмента на одной свече.

    Кроме коэффициентов хранятся абсолютные числа и медианы, из которых те
    получены. Это требование раздела 8 spec.md: в отчёте пользователь должен
    видеть и «в 4.1× выше обычного», и «8.4M USDT против обычных 2.0M».
    Иначе на этапе отчёта пришлось бы считать медианы второй раз.
    """

    rvol: float
    rtc: float
    ve: float
    change_pct: float

    volume: float             # оборот анализируемой свечи, USDT
    volume_median: float      # обычный оборот свечи за 48 ч
    trades: int
    trades_median: float
    range_pct: float          # диапазон свечи в процентах от цены
    range_pct_median: float


def true_range(candle: Candle, previous_close: float) -> float:
    """True Range по стандартной формуле (раздел 6.3 spec.md).

    Предыдущая цена закрытия нужна, чтобы учесть гэп: если свеча открылась
    заметно выше прошлого закрытия, её собственный размах High - Low
    занижает реальное движение.
    """
    return max(
        candle.high - candle.low,
        abs(candle.high - previous_close),
        abs(candle.low - previous_close),
    )


def normalized_true_range(candle: Candle, previous_close: float) -> float:
    """True Range в долях цены.

    Нормировка обязательна: без неё размах в 100 USDT у биткоина и у монеты
    за доллар выглядели бы одинаково, хотя это движения на 0.15 % и на 10 000 %.
    """
    return true_range(candle, previous_close) / candle.close


def compute_metrics(window: list[Candle]) -> Metrics | None:
    """Метрики по окну из WINDOW_CANDLES свечей.

    Раскладка окна:
        window[0]      — только источник Close_(i-1) для первой TR базы,
                         в расчёты не входит;
        window[1:-1]   — базовое окно, ровно BASELINE_CANDLES свечей;
        window[-1]     — анализируемая свеча t.

    Возвращает None, если медиана по какой-либо метрике равна нулю: делить
    на неё нельзя, а инструмент с нулевым обычным объёмом или нулевым обычным
    размахом анализировать всё равно бессмысленно. Решение о том, что с таким
    инструментом делать, принимает вызывающий код.
    """
    if len(window) != WINDOW_CANDLES:
        raise ValueError(
            f"окно должно быть длиной {WINDOW_CANDLES} свечей, получено {len(window)}"
        )

    baseline = window[1:-1]
    current = window[-1]
    previous = window[-2]

    volume_median = median(candle.quote_volume for candle in baseline)
    trades_median = median(candle.trades for candle in baseline)
    range_median = median(
        normalized_true_range(candle, window[index - 1].close)
        for index, candle in enumerate(window[1:-1], start=1)
    )
    current_range = normalized_true_range(current, previous.close)

    if 0 in (volume_median, trades_median, range_median, previous.close):
        return None

    return Metrics(
        rvol=current.quote_volume / volume_median,
        rtc=current.trades / trades_median,
        ve=current_range / range_median,
        change_pct=(current.close - previous.close) / previous.close * 100,
        volume=current.quote_volume,
        volume_median=volume_median,
        trades=current.trades,
        trades_median=trades_median,
        range_pct=current_range * 100,
        range_pct_median=range_median * 100,
    )


def compute_all(
    windows: dict[str, list[Candle]],
) -> tuple[dict[str, Metrics], list[str]]:
    """Метрики по всем инструментам.

    Второй элемент пары — символы, для которых метрику посчитать нельзя.
    Возвращается список, а не число: если такие инструменты появятся,
    захочется знать, какие именно, а не только сколько.
    """
    computed: dict[str, Metrics] = {}
    skipped: list[str] = []

    for symbol, window in windows.items():
        result = compute_metrics(window)
        if result is None:
            skipped.append(symbol)
        else:
            computed[symbol] = result

    return computed, skipped
