"""Тесты скоринга и отбора кандидатов."""

from dataclasses import replace

import pytest

from src.config import DEFAULT
from src.metrics import Metrics
from src.scoring import (
    is_candidate,
    normalized,
    score,
    select_candidates,
    triggered_metrics,
)


def make_metrics(rvol: float = 1.0, rtc: float = 1.0, ve: float = 1.0) -> Metrics:
    """Метрики с нужными коэффициентами. Абсолютные числа скорингу не важны."""
    return Metrics(
        rvol=rvol,
        rtc=rtc,
        ve=ve,
        change_pct=0.0,
        volume=1.0,
        volume_median=1.0,
        trades=1,
        trades_median=1.0,
        range_pct=1.0,
        range_pct_median=1.0,
    )


def at_thresholds() -> Metrics:
    """Инструмент ровно на всех трёх порогах."""
    return make_metrics(
        rvol=DEFAULT.rvol_threshold,
        rtc=DEFAULT.rtc_threshold,
        ve=DEFAULT.ve_threshold,
    )


def test_normalized_gives_one_at_the_threshold():
    assert normalized(3.0, threshold=3.0, cap=3.0) == 1.0
    assert normalized(6.0, threshold=3.0, cap=3.0) == 2.0


def test_normalized_stops_at_the_cap():
    assert normalized(9.0, threshold=3.0, cap=3.0) == 3.0
    assert normalized(1000.0, threshold=3.0, cap=3.0) == 3.0


def test_score_is_exactly_one_at_all_thresholds():
    """Веса в сумме дают единицу, поэтому «минимально интересный»
    инструмент получает ровно 1.0. Это делает шкалу score читаемой."""
    assert score(at_thresholds()) == pytest.approx(1.0)


def test_cap_makes_extreme_values_indistinguishable():
    """Следствие потолка, о котором надо помнить: RVOL 9 и RVOL 300
    вносят одинаковый вклад."""
    nine = make_metrics(rvol=9.0, rtc=2.5, ve=2.0)
    huge = make_metrics(rvol=300.0, rtc=2.5, ve=2.0)

    assert score(nine) == pytest.approx(score(huge))


def test_metric_exactly_at_the_threshold_counts_as_triggered():
    """Пороги интереса заданы нестрогим неравенством, в отличие от фильтров."""
    assert triggered_metrics(at_thresholds()) == 3


def test_two_triggered_metrics_make_a_candidate():
    two = make_metrics(rvol=5.0, rtc=3.0, ve=0.5)

    assert triggered_metrics(two) == 2
    assert is_candidate(two)


def test_one_triggered_metric_is_not_enough():
    """Всплеск объёма без роста числа сделок и без расширения диапазона —
    чаще всего одна крупная сделка, а не смена режима."""
    one = make_metrics(rvol=50.0, rtc=1.0, ve=1.0)

    assert triggered_metrics(one) == 1
    assert not is_candidate(one)


def test_candidates_are_sorted_by_score_descending():
    metrics_by_symbol = {
        "WEAK": make_metrics(rvol=3.0, rtc=2.5, ve=2.0),
        "STRONG": make_metrics(rvol=9.0, rtc=7.5, ve=6.0),
        "MIDDLE": make_metrics(rvol=6.0, rtc=5.0, ve=4.0),
    }

    result = select_candidates(metrics_by_symbol)

    assert [candidate.symbol for candidate in result] == ["STRONG", "MIDDLE", "WEAK"]
    assert [candidate.rank for candidate in result] == [1, 2, 3]


def test_non_candidates_are_left_out():
    metrics_by_symbol = {
        "GOOD": make_metrics(rvol=5.0, rtc=3.0, ve=1.0),
        "NOISE": make_metrics(rvol=50.0, rtc=1.0, ve=1.0),
    }

    result = select_candidates(metrics_by_symbol)

    assert [candidate.symbol for candidate in result] == ["GOOD"]


def test_list_is_truncated_to_top_n():
    identical = make_metrics(rvol=5.0, rtc=4.0, ve=3.0)
    metrics_by_symbol = {f"SYM{index:03d}": identical for index in range(40)}

    result = select_candidates(metrics_by_symbol)

    assert len(result) == DEFAULT.top_n


def test_ties_are_broken_by_symbol_so_runs_are_reproducible():
    """Одинаковый score не должен приводить к порядку, зависящему от того,
    в каком порядке инструменты пришли из предыдущего этапа."""
    identical = make_metrics(rvol=5.0, rtc=4.0, ve=3.0)

    forward = select_candidates({"BBB": identical, "AAA": identical})
    backward = select_candidates({"AAA": identical, "BBB": identical})

    assert [candidate.symbol for candidate in forward] == ["AAA", "BBB"]
    assert [candidate.symbol for candidate in backward] == ["AAA", "BBB"]


def test_capped_instruments_are_ordered_by_raw_rvol():
    """У обоих все три метрики упёрлись в потолок, score одинаков.
    Вперёд должен идти тот, у кого сильнее исходный сигнал, а не тот,
    чей символ раньше по алфавиту."""
    weaker = make_metrics(rvol=20.0, rtc=20.0, ve=20.0)
    stronger = make_metrics(rvol=500.0, rtc=20.0, ve=20.0)

    result = select_candidates({"AAA": weaker, "ZZZ": stronger})

    assert score(weaker) == pytest.approx(score(stronger))
    assert [candidate.symbol for candidate in result] == ["ZZZ", "AAA"]


def test_top_n_comes_from_the_config():
    identical = make_metrics(rvol=5.0, rtc=4.0, ve=3.0)
    metrics_by_symbol = {f"SYM{index:03d}": identical for index in range(40)}

    result = select_candidates(metrics_by_symbol, replace(DEFAULT, top_n=3))

    assert len(result) == 3
