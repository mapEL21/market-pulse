"""Тесты объяснений.

Проверяется состав причин, разделение на сработавшие и несработавшие,
формулировка итоговой фразы и требование раздела 8 spec.md о том, что
в объяснении есть и коэффициент, и абсолютное число.
"""

import pytest

from src.config import DEFAULT
from src.metrics import Metrics
from src.report import ats, build_reasons, compact, format_symbol, summary


def make_metrics(
    rvol: float = 1.0,
    rtc: float = 1.0,
    ve: float = 1.0,
    volume: float = 7_250_809.0,
    volume_median: float = 30_622.0,
    trades: int = 31_400,
    trades_median: float = 10_800.0,
    range_pct: float = 3.8,
    range_pct_median: float = 1.6,
) -> Metrics:
    return Metrics(
        rvol=rvol,
        rtc=rtc,
        ve=ve,
        change_pct=0.0,
        close=1.0,
        volume=volume,
        volume_median=volume_median,
        trades=trades,
        trades_median=trades_median,
        range_pct=range_pct,
        range_pct_median=range_pct_median,
    )


def all_triggered() -> Metrics:
    return make_metrics(rvol=4.1, rtc=2.9, ve=2.4)


def test_compact_switches_units_by_magnitude():
    assert compact(7_250_809) == "7.3M"
    assert compact(30_622) == "30.6K"
    assert compact(862) == "862"
    assert compact(2_400_000_000) == "2.4B"


def test_symbol_is_printed_as_a_futures_ticker():
    """Слэш означал бы спотовую пару, а анализируются только перпетуалы.
    Тикер пишется как на бирже, метка снимает двусмысленность."""
    assert format_symbol("COTIUSDT") == "COTIUSDT · PERP"
    assert "/" not in format_symbol("COTIUSDT")


def test_there_is_one_reason_per_metric():
    assert len(build_reasons(all_triggered())) == 3


def test_reason_shows_both_the_ratio_and_the_absolute_number():
    """Прямое требование раздела 8 spec.md. Закрепляю тестом, а не
    соглашением: голый коэффициент без абсолютных чисел неприемлем."""
    volume_reason = build_reasons(all_triggered())[0]

    assert "7.3M" in volume_reason.what          # сколько было на самом деле
    assert "4.1×" in volume_reason.ratio         # во сколько раз больше обычного
    assert "30.6K" in volume_reason.usual        # что для инструмента обычно


def test_triggered_flag_follows_the_thresholds():
    metrics = make_metrics(rvol=4.1, rtc=1.0, ve=2.4)

    volume, trades, spread = build_reasons(metrics)

    assert volume.triggered
    assert not trades.triggered
    assert spread.triggered


def test_threshold_text_comes_from_the_config():
    reasons = build_reasons(all_triggered())

    assert reasons[0].threshold == f"порог {DEFAULT.rvol_threshold}×"
    assert reasons[2].threshold == f"порог {DEFAULT.ve_threshold}×"


def test_ats_is_the_ratio_of_rvol_to_rtc():
    """Раздел 6.5 spec.md: величина выводится из уже посчитанных метрик."""
    assert ats(make_metrics(rvol=8.0, rtc=2.0)) == pytest.approx(4.0)


def test_ats_is_undefined_without_trades():
    assert ats(make_metrics(rtc=0.0)) is None


def test_summary_for_all_three_metrics():
    text = summary(all_triggered())

    assert "вышел из своего обычного режима" in text


def test_summary_for_volume_and_trades_without_spread():
    text = summary(make_metrics(rvol=4.1, rtc=2.9, ve=1.0))

    assert "диапазон свечи — нет" in text


def test_summary_for_volume_and_spread_without_trades():
    """Объём вырос, число сделок нет — работали крупные участники.
    Ради этого различия RVOL и RTC и разведены в разные метрики. Само
    различие даёт фраза про размер сделки, а не набор сработавших метрик."""
    text = summary(make_metrics(rvol=10.0, rtc=1.0, ve=2.4))

    assert "число сделок — нет" in text
    assert "Средняя сделка в 10.0× крупнее обычной." in text


def test_summary_for_trades_and_spread_without_volume():
    text = summary(make_metrics(rvol=1.0, rtc=2.9, ve=2.4))

    assert "оборот — нет" in text
    assert "мельче обычной" in text


def test_summary_never_calls_a_near_threshold_metric_usual():
    """Реальный случай из первого прогона: оборот в 2.9× выше обычного при
    пороге 3.0×. Назвать такой оборот обычным — прямая неправда, хотя
    формально метрика не сработала."""
    text = summary(make_metrics(rvol=2.9, rtc=2.7, ve=2.4))

    assert "остался обычным" not in text
    assert "оборот — нет" in text


def test_summary_mentions_a_bigger_average_trade():
    """RVOL вчетверо больше RTC — средняя сделка вчетверо крупнее обычной."""
    text = summary(make_metrics(rvol=8.0, rtc=2.0, ve=2.4))

    assert "Средняя сделка в 4.0× крупнее обычной." in text


def test_summary_mentions_a_smaller_average_trade():
    text = summary(make_metrics(rvol=2.0, rtc=8.0, ve=2.4))

    assert "Средняя сделка в 4.0× мельче обычной." in text


def test_summary_says_nothing_special_when_trade_size_is_unchanged():
    text = summary(make_metrics(rvol=4.0, rtc=4.0, ve=2.4))

    assert "Средний размер сделки почти не изменился." in text
