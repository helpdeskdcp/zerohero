"""Regression test: scalp_pipeline must pass target_1 to run_risk_engine so
its rr_min gate can actually evaluate scalp signals (previously always None
-> that gate silently never fired for any scalp trade). scalp_engine and
risk_engine are stubbed to isolate exactly this wiring."""
from app import scalp_pipeline


def test_risk_engine_receives_target_1_from_the_scalp_signal(fresh_db, monkeypatch):
    fake_sig = {
        "decision": "TRADE", "direction": "BUY", "symbol": "NIFTY", "market": "NSE",
        "instrument": "INDEX", "timeframe": "1m", "entry_zone": {"ref": 100.0},
        "stop_loss": 95.0, "target_1": 106.0, "target_2": 112.0,
        "reason": [], "calculations": {},
    }
    monkeypatch.setattr(scalp_pipeline, "run_scalp_engine", lambda inp: fake_sig)

    captured = {}
    def _fake_risk_engine(inp):
        captured.update(inp)
        return {"risk_status": "APPROVED", "allowed_quantity": 1, "reasons": []}
    monkeypatch.setattr(scalp_pipeline, "run_risk_engine", _fake_risk_engine)

    scalp_pipeline.run_scalp_pipeline({
        "market": "NSE", "symbol": "NIFTY", "instrument": "INDEX",
        "candles": [[0, 100, 101, 99, 100, 1000]],
        "account": {"capital": 500000, "risk_pct": 0.5},
        "risk_instrument": {"lot_size": 1},
        "limits": {"max_daily_loss_pct": 2, "max_trades": 20},
        "turning_point": False,
    })

    assert captured["signal"]["target_1"] == 106.0
    assert captured["signal"]["entry_ref"] == 100.0
    assert captured["signal"]["stop_loss"] == 95.0
