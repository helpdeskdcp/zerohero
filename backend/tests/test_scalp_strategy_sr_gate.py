"""
LIVE SR CONFIRMATION GATE in decide_from_context (opt-in, default OFF).

Mirrors tests/test_scalp_strategy_chain_gate.py's structure exactly for the
new SR gate: proves (a) it's a no-op when disabled (byte-identical plan),
(b) CONFIRM keeps the trade and can optionally bump confidence, (c)
CONTRADICT vetoes only a marginal setup, else just a confidence haircut,
(d) it never touches probability/EV/entry/SL/targets, (e) live_sr is always
computed and attached for observability even when the gate itself is off.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.engines import scalp_strategy as ss  # noqa: E402


def _chain():
    rows = []
    for i in range(-3, 4):
        k = 100.0 + i
        rows.append({"strike": k, "ce": {"ltp": 5.0, "oi": 3000, "oi_chg": 0},
                     "pe": {"ltp": 5.0, "oi": 3000, "oi_chg": 0}})
    return rows


def _stub_qualified_buy(monkeypatch, *, state_score=72.0):
    monkeypatch.setattr(ss, "compute_sr", lambda *a, **k: {
        "status": "OK", "price": 100.0, "atr": 2.0,
        "support": {"level": 96.0, "strength": 0.6},
        "resistance": {"level": 108.0, "strength": 0.6}})
    monkeypatch.setattr(ss, "detect_regime", lambda *a, **k: {
        "regime": "TRENDING_UP", "confidence": 0.6})
    monkeypatch.setattr(ss, "mtf_alignment", lambda *a, **k: {
        "alignment": 0.5, "magnitude": 40.0, "conflict": False, "htf_dominant": False})
    monkeypatch.setattr(ss, "classify", lambda *a, **k: {
        "state": "SUPPORT_REVERSAL", "direction": "BULLISH", "state_score": state_score,
        "anchor": {"level": 96.0, "side": "SUPPORT"}, "components": {},
        "false_risk": {"verdict": "CLEAN", "score": 100.0}, "reason": ["stub"], "roc_pct": 0.2})
    leg = {"opt_type": "CE", "ltp": 90.0, "strike": 100.0, "token": "T1", "tradingsymbol": "X",
           "expiry": "2026-09-30", "sr": {"support": 80.0, "resistance": 120.0, "atr": 6.0},
           "translation_score": 0.7, "translation": {"method": "greeks", "expected_premium_move": 12.0},
           "quality_score": 80.0}
    monkeypatch.setattr(ss, "analyse_leg", lambda *a, **k: dict(leg))
    monkeypatch.setattr(ss, "select_option",
                        lambda *a, **k: {**leg, "final_quality": 80.0, "atm_proximity": 0.9})
    monkeypatch.setattr(ss, "_plan_from_leg", lambda *a, **k: {
        "entry": 90.0, "stop_loss": 78.0, "target_1": 114.0, "target_2": 138.0,
        "trailing_stop": 5.0, "max_hold_sec": 1500})
    # refresh_and_store needs real bars to find zones; stub it so the gate
    # tests exercise the WIRING (veto/haircut/bump/passthrough), not the SR
    # computation itself (already covered by tests/sr_dynamic/test_signal_confirm.py).
    monkeypatch.setattr(ss, "refresh_and_store", lambda *a, **k: _FAKE_LIVE_SR)


class _FakeLiveSR:
    state = "AT_SUPPORT"

    def to_dict(self):
        return {"symbol": "NIFTY", "state": self.state}


_FAKE_LIVE_SR = _FakeLiveSR()


def _bars():
    return {"5m": [[0, 100, 101, 99, 100, 1000]] * 30}


def _run(monkeypatch, cfg, *, state_score=72.0, sr_verdict="NEUTRAL"):
    _stub_qualified_buy(monkeypatch, state_score=state_score)
    monkeypatch.setattr(ss, "evaluate_sr_confirmation", lambda *a, **k: {
        "verdict": sr_verdict, "reason": f"stub:{sr_verdict}", "sr_score": 60.0,
        "sr_confidence": 70.0, "positives": [], "negatives": [], "components": {}})
    return ss.decide_from_context(_bars(), _chain(), atm=100.0, calib=None, config=cfg)


_PLAN_KEYS = ("entry", "stop_loss", "target_1", "target_2", "probability", "ev", "ev_r", "rr")


def test_gate_off_is_a_noop(monkeypatch):
    base = _run(monkeypatch, {"filters": {}}, sr_verdict="CONTRADICT")
    off = _run(monkeypatch, {"filters": {}, "sr_gates": {"enabled": False}}, sr_verdict="CONTRADICT")
    assert base["decision"] == off["decision"] == "BUY_CE"
    assert off.get("sr_confirmation") is None
    assert {k: base.get(k) for k in _PLAN_KEYS} == {k: off.get(k) for k in _PLAN_KEYS}


def test_live_sr_attached_even_when_gate_disabled(monkeypatch):
    d = _run(monkeypatch, {"filters": {}}, sr_verdict="CONFIRM")
    assert d.get("sr_confirmation") is None      # gate itself is off
    assert d.get("live_sr") == {"symbol": "NIFTY", "state": "AT_SUPPORT"}   # observability unaffected


def test_gate_on_confirm_keeps_buy_and_attaches_verdict(monkeypatch):
    d = _run(monkeypatch, {"filters": {}, "sr_gates": {"enabled": True}}, sr_verdict="CONFIRM")
    assert d["decision"] == "BUY_CE"
    assert d["sr_confirmation"]["verdict"] == "CONFIRM"
    d_off = _run(monkeypatch, {"filters": {}}, sr_verdict="CONFIRM")
    assert {k: d.get(k) for k in _PLAN_KEYS} == {k: d_off.get(k) for k in _PLAN_KEYS}


def test_gate_on_confirm_can_bump_confidence_when_opted_in(monkeypatch):
    base = _run(monkeypatch, {"filters": {}}, sr_verdict="CONFIRM")
    up = _run(monkeypatch, {"filters": {}, "sr_gates": {"enabled": True, "confirm_bumps_confidence": True}},
              sr_verdict="CONFIRM")
    order = ["LOW", "MEDIUM", "HIGH"]
    assert order.index(up["confidence"]) >= order.index(base["confidence"])
    assert {k: base.get(k) for k in _PLAN_KEYS} == {k: up.get(k) for k in _PLAN_KEYS}


def test_gate_on_contradiction_vetoes_a_marginal_setup(monkeypatch):
    d = _run(monkeypatch, {"filters": {}, "sr_gates": {"enabled": True, "marginal_score": 95.0}},
             sr_verdict="CONTRADICT")
    assert d["decision"] == "NO_TRADE"
    assert "SR confirmation contradicts" in d["reason"]
    assert d["sr_confirmation"]["verdict"] == "CONTRADICT"


def test_gate_on_contradiction_only_haircuts_a_strong_setup(monkeypatch):
    base = _run(monkeypatch, {"filters": {}}, sr_verdict="CONTRADICT")
    d = _run(monkeypatch, {"filters": {}, "sr_gates": {"enabled": True}}, sr_verdict="CONTRADICT")
    assert d["decision"] in ("BUY_CE", "WATCH")
    order = ["LOW", "MEDIUM", "HIGH"]
    assert order.index(d["confidence"]) <= order.index(base["confidence"])
    if d["decision"] == "BUY_CE":
        assert {k: base.get(k) for k in _PLAN_KEYS} == {k: d.get(k) for k in _PLAN_KEYS}


def test_gate_on_insufficient_sr_is_a_noop(monkeypatch):
    base = _run(monkeypatch, {"filters": {}}, sr_verdict="INSUFFICIENT")
    d = _run(monkeypatch, {"filters": {}, "sr_gates": {"enabled": True}}, sr_verdict="INSUFFICIENT")
    assert d["decision"] == base["decision"] == "BUY_CE"
    assert {k: base.get(k) for k in _PLAN_KEYS} == {k: d.get(k) for k in _PLAN_KEYS}
