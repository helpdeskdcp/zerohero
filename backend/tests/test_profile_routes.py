"""app.api.profile_routes -- direct function calls (this repo's convention,
no TestClient). Read-only diagnostics, no broker calls, no secrets."""
import pytest
from fastapi import HTTPException

from app.api import profile_routes as pr


def test_api_profiles_returns_all_five_watchlist_symbols():
    out = pr.api_profiles()
    assert set(out["symbols"].keys()) == {"NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL"}


def test_api_profile_one_known_symbol():
    out = pr.api_profile_one("naturalgas")
    assert out["symbol"] == "NATURALGAS"
    assert out["cost_model_status"] == "OK"


def test_api_profile_one_unknown_symbol_404s():
    with pytest.raises(HTTPException) as exc:
        pr.api_profile_one("DOGECOIN")
    assert exc.value.status_code == 404


def test_api_profile_effective_deterministic():
    a = pr.api_profile_effective("BANKNIFTY", regime="TRENDING_UP", is_expiry_day=False)
    b = pr.api_profile_effective("BANKNIFTY", regime="TRENDING_UP", is_expiry_day=False)
    assert a == b
    assert a["symbol"] == "BANKNIFTY" and a["regime"] == "TRENDING_UP"


def test_api_regimes_lists_expiry_day_and_normal_day():
    out = pr.api_regimes()
    assert "EXPIRY_DAY" in out["regimes"] and "NORMAL_DAY" in out["regimes"]
    assert out["regimes"]["EXPIRY_DAY"]["status"] == "DERIVED"


def test_api_calibration_status_distinguishes_calibrated_and_uncalibrated():
    out = pr.api_calibration_status()
    assert out["symbols"]["NATURALGAS"]["cost_model_status"] == "OK"
    assert out["symbols"]["CRUDEOIL"]["cost_model_status"] == "OK"
    for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
        assert out["symbols"][sym]["cost_model_status"] == "UNCALIBRATED"


def test_api_ai_status_never_exposes_a_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-should-never-appear")
    monkeypatch.setenv("GROQ_MODEL", "test/model-a")
    out = pr.api_ai_status()
    assert out["available"] is True
    assert out["ai_provider"] == "groq"
    assert out["ai_model"] == "test/model-a"
    assert out["ai_config_status"] == "OK"
    assert "gsk-should-never-appear" not in str(out)
    assert "metrics" in out


def test_api_ai_status_unavailable_when_no_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    out = pr.api_ai_status()
    assert out["available"] is False
    assert out["ai_config_status"] == "CONFIG_REQUIRED"
    assert out["ai_model"] is None


def test_api_data_quality_empty_symbol_returns_zero_report(fresh_db):
    out = pr.api_data_quality("NIFTY")
    assert out["total_rows"] == 0 and out["valid_rows"] == 0 and out["contaminated_rows"] == 0


def test_api_data_quality_reports_contaminated_sensex_rows(fresh_db):
    fresh_db.insert_trade({
        "trade_id": "T1", "signal_id": "S1", "opened_ts": "2026-09-17T05:00:00+00:00",
        "closed_ts": "2026-09-17T05:30:00+00:00", "status": "CLOSED", "result": "FLAT",
        "market": "BSE", "underlying": "SENSEX", "instrument": "OPTION", "direction": "BUY",
        "entry": 100.0, "exit_price": 100.0, "exit_reason": "TIME_NODATA",
        "strategy": "AUTOSCALP", "pnl": 0.0,
    })
    out = pr.api_data_quality("SENSEX")
    assert out["total_rows"] == 1 and out["contaminated_rows"] == 1
    assert out["bse_pre_fix_count"] == 1
