"""PHASE 8/9 — immutable entry-feature snapshot + outcome record."""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))


def _fresh_db(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("CHANAKYA_DB_PATH", os.path.join(d, "t.db"))
    import importlib, app.db as db
    importlib.reload(db)
    db.init_db()
    return db


SIG = {
    "decision": "BUY_CE", "expiry": "09SEP2026", "strike": 24000.0, "token": "T1",
    "tradingsymbol": "NIFTY09SEP2624000CE", "entry": 96.0, "stop_loss": 84.0,
    "target_1": 115.0, "target_2": 126.0, "rr": 1.6, "ev": 0.7,
    "trailing_stop": 6.0, "max_hold_sec": 1500,
    "vwap": 24010.0, "vwap_status": "available", "atr": 22.0,
    "momentum": 0.3, "state_score": 61.0, "signal_score": 58.0,
    "probability": 0.61, "confidence": "MEDIUM", "regime": "TRENDING_UP",
    "signal_type": "MOMENTUM_CONTINUATION", "support": 23900.0, "resistance": 24100.0,
    "support_strength": 70, "resistance_strength": 60, "mtf_alignment": 34,
    "false_risk": "LOW", "gex_flip": 23950.0, "gex_pin": 24200.0,
    "gex_regime_sign": 1, "gex_sigma": 0.11, "calib_version": "prior",
    "component_scores": {"a": 1}, "model_version": "scalp-strategy-v1",
}
def _leg(oi, d):
    return {"ltp": 96.0, "oi": oi, "oi_chg": 1000, "vol_delta": 12000,
            "delta": d, "gamma": 0.0013, "theta": -14.2, "vega": 11.1, "iv": 0.106,
            "greeks_source": "BROKER", "oi_status": "AVAILABLE"}
CHAIN = [{"strike": k, "ce": _leg(2850000 if k == 24000.0 else 900000, 0.55),
          "pe": _leg(2600000 if k == 24000.0 else 800000, -0.45)}
         for k in (23900.0, 23950.0, 24000.0, 24050.0, 24100.0)]


def test_entry_features_written_once_and_immutable(monkeypatch):
    db = _fresh_db(monkeypatch)
    from app.autoscalp.trade_features import build_entry_features
    from app.autoscalp.runner import _chain_oi_quality
    oiq = _chain_oi_quality(CHAIN)
    feat = build_entry_features(sig=SIG, chain=CHAIN, sym="NIFTY", market="NSE",
                                trade_id="TRD-1", signal_id="ASC-1", underlying_ltp=24010.0,
                                oi_quality=oiq, data_quality={"groups": {"PRICE": "AVAILABLE"}, "score": 0.9})
    assert db.insert_trade_entry_features(feat) is True
    # second write is refused (immutable)
    tampered = {**feat, "probability": 0.99}
    assert db.insert_trade_entry_features(tampered) is False
    got = db.get_trade_entry_features("TRD-1")
    assert got["probability"] == 0.61                      # original preserved
    assert got["delta"] == 0.55 and got["gamma"] == 0.0013 and got["greeks_source"] == "BROKER"
    assert got["oi"] == 2850000 and got["pcr"] is not None
    assert got["atr_pct"] is not None
    # derived-not-surfaced indicators are NULL with a reason, never 0
    assert got["rsi"] is None and got["macd"] is None
    assert "DERIVED_NOT_SURFACED" in got["missing_reasons"]
    assert got["planned_t3"] is None and "NOT_IMPLEMENTED" in got["missing_reasons"]


def test_entry_features_mcx_greeks_null_not_zero(monkeypatch):
    db = _fresh_db(monkeypatch)
    from app.autoscalp.trade_features import build_entry_features
    mcx_chain = [{"strike": 280.0,
                  "ce": {"ltp": 12.0, "oi": 50000, "greeks_source": "UNAVAILABLE",
                         "delta": None, "gamma": None, "theta": None, "vega": None, "iv": None,
                         "oi_status": "AVAILABLE"},
                  "pe": {"ltp": 11.0, "greeks_source": "UNAVAILABLE"}}]
    feat = build_entry_features(sig={**SIG, "strike": 280.0, "expiry": "25SEP2026"},
                                chain=mcx_chain, sym="NATURALGAS", market="MCX",
                                trade_id="TRD-2", signal_id="ASC-2", underlying_ltp=281.0,
                                oi_quality={"pcr": None, "max_pain": None, "quality_status": "INSUFFICIENT_OI",
                                            "coverage_ratio": 0.5},
                                data_quality={"groups": {}, "score": 0.6})
    db.insert_trade_entry_features(feat)
    got = db.get_trade_entry_features("TRD-2")
    assert got["delta"] is None and got["theta"] is None and got["vega"] is None
    assert "BROKER_UNSUPPORTED" in got["missing_reasons"]
    assert got["pcr"] is None and "INSUFFICIENT_OI" in got["missing_reasons"]


def test_exit_outcome_and_training_join(monkeypatch):
    db = _fresh_db(monkeypatch)
    from app.autoscalp.trade_features import build_entry_features, build_exit_outcome
    from app.autoscalp.runner import _chain_oi_quality
    feat = build_entry_features(sig=SIG, chain=CHAIN, sym="NIFTY", market="NSE",
                                trade_id="TRD-9", signal_id="ASC-9", underlying_ltp=24010.0,
                                oi_quality=_chain_oi_quality(CHAIN), data_quality={"groups": {}, "score": 0.9})
    db.insert_trade_entry_features(feat)
    closed = {"trade_id": "TRD-9", "signal_id": "ASC-9", "underlying": "NIFTY",
              "option_type": "CE", "strategy": "AUTOSCALP",
              "opened_ts": "2026-09-03T04:00:00Z", "closed_ts": "2026-09-03T04:12:00Z",
              "entry": 96.0, "exit_price": 115.0, "stop_loss": 84.0, "target_1": 115.0,
              "exit_reason": "TARGET", "result": "WIN", "pnl": 19.0, "mfe": 20.0, "mae": 3.0}
    db.insert_trade_exit_outcome(build_exit_outcome(updated=closed, entry_feat=db.get_trade_entry_features("TRD-9")))
    rows = db.list_clean_training_rows()
    assert len(rows) == 1
    r = rows[0]
    assert r["y_outcome"] == "WIN" and r["y_t1_before_sl"] == 1
    assert r["delta"] == 0.55                              # entry feature joined
    assert abs(r["y_r_multiple"] - (19.0 / 12.0)) < 1e-3
