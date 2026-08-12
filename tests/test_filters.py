"""Тесты отсева инструментов.

Проверяются пороги из раздела 5 spec.md и порядок срабатывания причин.
Сеть не используется: инструменты собираются вручную.
"""

from dataclasses import replace

from src.config import DEFAULT
from src.exchanges.binance import Instrument
from src.filters import apply_filters, rejection_reason


def make_instrument(**overrides) -> Instrument:
    """Инструмент, проходящий все фильтры.

    Нужное поле портится через overrides — так в каждом тесте видно ровно то
    условие, которое он проверяет, и ничего лишнего.
    """
    defaults = {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "quote_volume_24h": 100_000_000.0,
        "trades_24h": 500_000,
        "last_price": 64_000.0,
        "change_pct_24h": 1.0,
    }
    return Instrument(**(defaults | overrides))


def test_good_instrument_passes():
    assert rejection_reason(make_instrument()) is None


def test_low_turnover_is_rejected():
    assert rejection_reason(make_instrument(quote_volume_24h=4_999_999.0)) == "volume"


def test_few_trades_is_rejected():
    assert rejection_reason(make_instrument(trades_24h=9_999)) == "trades"


def test_inactive_status_is_rejected():
    assert rejection_reason(make_instrument(status="SETTLING")) == "status"


def test_thresholds_themselves_pass():
    """В spec.md неравенства строгие: «< 5 000 000» и «< 10 000».

    Значение ровно на пороге должно проходить. Легко случайно написать <=
    и молча потерять инструменты на границе.
    """
    on_the_edge = make_instrument(quote_volume_24h=5_000_000.0, trades_24h=10_000)
    assert rejection_reason(on_the_edge) is None


def test_status_is_checked_before_liquidity():
    """Инструмент, снятый с торгов, должен считаться снятым с торгов,
    даже если у него заодно нулевой оборот."""
    delisted_and_empty = make_instrument(
        status="SETTLING", quote_volume_24h=0.0, trades_24h=0
    )
    assert rejection_reason(delisted_and_empty) == "status"


def test_thresholds_come_from_the_config():
    """Смысл конфига: пороги можно поменять, не трогая код фильтров.
    Это же понадобится на этапе 10, чтобы пересчитать прогоны с другими
    значениями и сравнить результаты."""
    strict = replace(DEFAULT, min_quote_volume_24h=200_000_000)
    instrument = make_instrument(quote_volume_24h=100_000_000.0)

    assert rejection_reason(instrument) is None
    assert rejection_reason(instrument, strict) == "volume"


def test_apply_filters_splits_list_and_counts_reasons():
    instruments = [
        make_instrument(symbol="OKAY1"),
        make_instrument(symbol="OKAY2"),
        make_instrument(symbol="THIN", quote_volume_24h=1_000_000.0),
        make_instrument(symbol="DEAD", trades_24h=500),
        make_instrument(symbol="GONE", status="SETTLING"),
    ]

    passed, stats = apply_filters(instruments)

    assert [instrument.symbol for instrument in passed] == ["OKAY1", "OKAY2"]
    assert stats.total == 5
    assert stats.passed == 2
    assert stats.rejected == {"status": 1, "volume": 1, "trades": 1}
    # Прошедшие плюс отсеянные дают исходное количество.
    assert stats.passed + sum(stats.rejected.values()) == stats.total
