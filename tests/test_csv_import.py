import pytest

from smarttrade.data.csv_importer import CsvFormatError, import_csv_text, parse_csv_text, parse_timestamp
from smarttrade.models import AssetClass

YAHOO = """Date,Open,High,Low,Close,Adj Close,Volume
2024-01-02,100,102,99,101,101,1000
2024-01-03,101,103,100,102,102,1100
2024-01-04,102,104,101,103,103,1200
"""

SEMICOLON = """timestamp;open;high;low;close;volume
1704153600;100;102;99;101;1000
1704240000;101;103;100;102;1100
"""

MILLIS = """time,o,h,l,c,v
1704153600000,100,102,99,101,1000
1704240000000,101,103,100,102,1100
"""


def test_yahoo_layout():
    bars, warnings = parse_csv_text(YAHOO)
    assert len(bars) == 3
    assert bars[0].close == 101
    assert bars[0].ts.isoformat() == "2024-01-02T00:00:00+00:00"
    assert warnings == []


def test_semicolon_and_epoch_seconds():
    bars, _ = parse_csv_text(SEMICOLON)
    assert len(bars) == 2
    assert bars[0].ts.isoformat() == "2024-01-02T00:00:00+00:00"


def test_epoch_milliseconds_and_short_columns():
    bars, _ = parse_csv_text(MILLIS)
    assert len(bars) == 2
    assert bars[1].high == 103


def test_missing_required_column():
    with pytest.raises(CsvFormatError):
        parse_csv_text("Date,Open,High,Low\n2024-01-02,1,2,0\n")


def test_missing_timestamp_column():
    with pytest.raises(CsvFormatError):
        parse_csv_text("Open,High,Low,Close\n1,2,0,1\n")


def test_empty_file():
    with pytest.raises(CsvFormatError):
        parse_csv_text("   ")


def test_bad_rows_skipped_with_warning():
    text = YAHOO + "not-a-date,1,2,0,1,1,1\n"
    bars, warnings = parse_csv_text(text)
    assert len(bars) == 3
    assert any("could not be parsed" in w for w in warnings)


def test_timezone_conversion():
    ts = parse_timestamp("2024-06-01 09:30", "America/New_York")
    assert ts.isoformat() == "2024-06-01T13:30:00+00:00"


def test_import_creates_historical_dataset():
    result = import_csv_text(YAHOO, "test", "Test Co", AssetClass.STOCK)
    assert result.dataset.symbol == "TEST"
    assert result.dataset.status.value == "HISTORICAL"
    assert result.dataset.source == "csv-import"
    assert result.dataset.retrieved_at is not None
    assert len(result.bars) == 3
    assert any("usable bars" in i for i in result.report.issues)  # too few bars noted
