"""Тесты страницы.

Проверяется не разметка, а то, что в неё попали правильные данные:
верстать можно как угодно, а вот потерянное число или неэкранированный
символ ломают отчёт по-настоящему.
"""

from src.analysis import QUERIES
from src.config import DEFAULT
from src.metrics import Metrics
from src.report_html import build_page, render_card
from src.scoring import Observation
from src.storage import (
    connect,
    observation_ids,
    save_observations,
    save_outcomes,
    save_run,
)

CANDLE_MS = 1786575600000


def make_metrics(**overrides) -> Metrics:
    defaults = {
        "rvol": 4.1,
        "rtc": 2.9,
        "ve": 2.4,
        "change_pct": 6.46,
        "close": 0.3712,
        "volume": 7_250_809.0,
        "volume_median": 30_622.0,
        "trades": 31_400,
        "trades_median": 10_800.0,
        "range_pct": 3.8,
        "range_pct_median": 1.6,
    }
    return Metrics(**(defaults | overrides))


def make_row(symbol: str = "APRUSDT", **overrides) -> tuple:
    """Строка в том же порядке, что возвращает storage.run_candidates."""
    metrics = make_metrics(**overrides)
    return (
        symbol,
        1,
        3.0,
        metrics.rvol,
        metrics.rtc,
        metrics.ve,
        metrics.close,
        metrics.change_pct,
        metrics.volume,
        metrics.volume_median,
        metrics.trades,
        metrics.trades_median,
        metrics.range_pct,
        metrics.range_pct_median,
        851_000_000.0,
    )


def test_card_shows_both_the_ratio_and_the_absolute_numbers():
    """То же требование раздела 8 spec.md, что и для консоли: голый
    коэффициент без абсолютных чисел неприемлем."""
    card = render_card(make_row())

    assert "7.3M" in card          # объём свечи
    assert "4.1×" in card          # во сколько раз больше обычного
    assert "30.6K" in card         # что для инструмента обычно
    assert "851.0M" in card        # оборот за 24 ч
    assert "score 3.00" in card


def test_card_marks_the_direction_of_the_move():
    up = render_card(make_row(change_pct=6.46))
    down = render_card(make_row(change_pct=-6.46))

    assert "+6.46 %" in up and 'class="num up"' in up
    assert "-6.46 %" in down and 'class="num down"' in down


def test_card_without_trade_counts_has_no_trades_line():
    """У OKX числа сделок нет — строки о них не должно быть вовсе."""
    card = render_card(
        make_row("APR-USDT-SWAP", rtc=None, trades=None, trades_median=None)
    )

    assert "Сделок" not in card
    assert "не публикует" in card


def test_unsafe_characters_are_escaped():
    """Символ из данных не должен превращаться в разметку."""
    card = render_card(make_row("<script>alert(1)</script>"))

    assert "<script>" not in card
    assert "&lt;script&gt;" in card


def test_page_is_built_on_an_empty_database():
    """Страница должна собираться до первого прогона, а не падать."""
    connection = connect(":memory:")
    try:
        page = build_page(connection)
    finally:
        connection.close()

    assert "Market Pulse" in page
    assert "Прогонов пока нет" in page


def test_every_analysis_query_actually_groups():
    """Защита от ловушки SQLite: если псевдоним колонки совпадает с именем
    настоящей колонки таблицы, GROUP BY возьмёт колонку, а не псевдоним.
    Запрос не упадёт — он вернёт строку на каждое наблюдение вместо
    нескольких групп, и страница раздуется до мегабайтов.

    Наблюдений здесь заведомо больше, чем групп в любом разделе.
    """
    connection = connect(":memory:")
    try:
        run_id = save_run(
            connection,
            exchange="BINANCE",
            candle_open_ms=CANDLE_MS,
            total_symbols=40,
            passed_filters=40,
            analysed_symbols=40,
            candidates_count=0,
            config=DEFAULT,
            source="backtest",
        )
        observations = [
            Observation(
                symbol=f"SYM{index:03d}",
                score=1.0,
                triggered=0,
                metrics=make_metrics(rvol=1.0 + index / 10),
                rank=None,
            )
            for index in range(40)
        ]
        save_observations(
            connection,
            run_id,
            "BINANCE",
            observations,
            {observation.symbol: 1.0 for observation in observations},
        )
        save_outcomes(
            connection,
            [
                (observation_id, 1.0, 1.0, 1.0, 1.0)
                for observation_id in observation_ids(connection, run_id).values()
            ],
        )

        for query in QUERIES:
            rows = connection.execute(query.sql).fetchall()
            assert len(rows) <= 10, f"{query.title}: {len(rows)} строк"
    finally:
        connection.close()


def test_page_shows_the_latest_run_of_each_exchange():
    connection = connect(":memory:")
    try:
        run_id = save_run(
            connection,
            exchange="BINANCE",
            candle_open_ms=CANDLE_MS,
            total_symbols=527,
            passed_filters=38,
            analysed_symbols=38,
            candidates_count=6,
            config=DEFAULT,
        )
        save_observations(
            connection,
            run_id,
            "BINANCE",
            [
                Observation(
                    symbol="APRUSDT",
                    score=3.0,
                    triggered=3,
                    metrics=make_metrics(),
                    rank=1,
                )
            ],
            {"APRUSDT": 851_000_000.0},
        )
        page = build_page(connection)
    finally:
        connection.close()

    assert "APRUSDT" in page
    assert "live" in page               # метка источника прогона
    assert "Прогонов пока нет" in page  # у OKX прогонов нет
