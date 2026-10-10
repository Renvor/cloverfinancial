from datetime import datetime, timedelta, timezone

from smarttrade.data.validation import validate_bars
from smarttrade.models import Bar

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def bar(i: int, o=100, h=101, lo=99, c=100.5, v=1000, days=1) -> Bar:
    return Bar(T0 + timedelta(days=i * days), o, h, lo, c, v)


def test_empty_input():
    clean, report = validate_bars([])
    assert clean == []
    assert "No rows" in report.issues[0]


def test_duplicates_removed_and_sorted():
    bars = [bar(2), bar(0), bar(1), bar(1)]
    clean, report = validate_bars(bars, "1d", is_crypto=True)
    assert [b.ts for b in clean] == [bar(0).ts, bar(1).ts, bar(2).ts]
    assert report.duplicates_removed == 1
    assert report.out_of_order_fixed


def test_invalid_rows_dropped():
    bars = [
        bar(0),
        bar(1, o=-5),  # negative price
        bar(2, h=90, lo=99),  # high below low
        bar(3, o=105, h=101, lo=99, c=100),  # open outside range
        bar(4, c=float("nan")),  # missing
        bar(5, v=-1),  # negative volume
        bar(6),
    ]
    clean, report = validate_bars(bars, "1d", is_crypto=True)
    assert len(clean) == 2
    assert report.invalid_rows == 5
    assert report.quality_score < 1.0


def test_gap_detection_for_crypto_daily():
    bars = [bar(0), bar(1), bar(2), bar(6), bar(7)]
    clean, report = validate_bars(bars, "1d", is_crypto=True)
    assert report.gaps_detected == 1
    assert any("gap" in i for i in report.issues)


def test_weekend_gap_is_not_a_gap_for_stocks():
    fri = datetime(2024, 1, 5, tzinfo=timezone.utc)
    bars = [Bar(fri - timedelta(days=1), 100, 101, 99, 100, 1), Bar(fri, 100, 101, 99, 100, 1), Bar(fri + timedelta(days=3), 100, 101, 99, 100, 1)]
    _, report = validate_bars(bars, "1d", is_crypto=False)
    assert report.gaps_detected == 0


def test_inconsistent_interval_flagged():
    bars = [bar(0), Bar(T0 + timedelta(hours=3), 100, 101, 99, 100, 1), bar(1), bar(2)]
    _, report = validate_bars(bars, "1d", is_crypto=True)
    assert report.inconsistent_intervals == 1


def test_too_few_bars_noted():
    _, report = validate_bars([bar(i) for i in range(10)], "1d", is_crypto=True)
    assert any("usable bars" in i for i in report.issues)
    assert report.quality_score < 0.8


def test_extreme_jump_flagged_not_dropped():
    bars = [bar(0, c=100), bar(1, o=200, h=201, lo=199, c=200), bar(2, o=200, h=201, lo=199, c=200)]
    clean, report = validate_bars(bars, "1d", is_crypto=True)
    assert len(clean) == 3
    assert any("60%" in i for i in report.issues)
