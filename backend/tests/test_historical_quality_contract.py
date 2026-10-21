"""Completeness is per contract, never the union of CE/PE or broker clocks."""
import pytest
from app.hist_options.quality import build_report


def row(minute, **changes):
    return {"underlying": "NIFTY", "exchange": "NSE", "expiry": "2026-09-08",
            "strike": 24000, "option_type": "CE", "timeframe": "1m", "source": "DHAN",
            "timestamp": f"2026-09-02T04:{minute:02}:00Z", "greek_source": "UNAVAILABLE",
            "greek_inputs": {"unavailable_reason": "NO_EXPIRY_DATE"}, **changes}


@pytest.mark.parametrize("dimension,value", [("option_type", "PE"), ("expiry", "2026-09-15"),
                                            ("source", "ANGELONE"), ("strike", 24100)])
def test_other_contract_cannot_hide_an_interior_gap(dimension, value):
    rows = [row(i) for i in (10, 11, 12)] + [row(i, **{dimension: value}) for i in (10, 12)]
    report = build_report(rows)
    assert report["duplicate_records"] == 0
    assert report["missing_bars_total"] == 1
    assert report["completeness_pct"] == pytest.approx(100 * 5 / 6, abs=0.01)
    assert len(report["per_day"]) == 2
    assert report["unavailable_reasons"]["NO_EXPIRY_DATE"] == 5


def test_timeframe_defines_the_series_grid():
    report = build_report([row(i, timeframe="5m") for i in (10, 15, 20)])
    assert report["missing_bars_total"] == 0 and report["completeness_pct"] == 100
