"""
OPTION-CHAIN GATE in decide_from_context (opt-in, default OFF).

Proves: the ATM-window PCR / OI-writing bias is (a) computed from the
point-in-time chain the engine already receives, (b) reaches the final decision
path as an interpretable confirmation / contradiction / confidence gate,
(c) NEVER touches probability / EV / entry / SL / targets, (d) is a no-op when
disabled or when OI coverage is thin, (e) never crashes on malformed input.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.engines import scalp_strategy as ss  # noqa: E402


# --------------------------------------------------------------------------- #
#  _chain_bias -- pure classifier                                              #
# --------------------------------------------------------------------------- #
_G = {"bias_window": 3, "min_coverage": 0.6, "pcr_confirm": 1.30, "pcr_contra": 0.70}


def _chain(pcr_side, *, oi_chg_ce=0, oi_chg_pe=0, n=7, base=100.0, step=1.0, cover=True):
    """pcr_side: 'PUT_HEAVY' (PCR>1) / 'CALL_HEAVY' (PCR<1) / 'BALANCED'."""
    ce_oi, pe_oi = {"PUT_HEAVY": (1000, 5000), "CALL_HEAVY": (5000, 1000),
                    "BALANCED": (3000, 3000)}[pcr_side]
    rows = []
    for i in range(-(n // 2), n // 2 + 1):
        k = base + i * step
        ce = {"ltp": 5.0, "oi": (ce_oi if cover else None), "oi_chg": oi_chg_ce}
        pe = {"ltp": 5.0, "oi": (pe_oi if cover else None), "oi_chg": oi_chg_pe}
        rows.append({"strike": k, "ce": ce, "pe": pe})
    return rows


def test_chain_bias_confirms_bullish_on_put_heavy_pcr():
    b = ss._chain_bias(_chain("PUT_HEAVY"), 100.0, "CE", _G)
    assert b["verdict"] == "CONFIRM" and b["pcr"] >= 1.3


def test_chain_bias_contradicts_bullish_on_call_heavy_pcr():
    b = ss._chain_bias(_chain("CALL_HEAVY"), 100.0, "CE", _G)
    assert b["verdict"] == "CONTRADICT" and b["pcr"] <= 0.7


def test_chain_bias_confirms_bearish_on_call_heavy_pcr():
    b = ss._chain_bias(_chain("CALL_HEAVY"), 100.0, "PE", _G)
    assert b["verdict"] == "CONFIRM"


def test_chain_bias_fresh_put_writing_confirms_bullish_even_at_neutral_pcr():
    b = ss._chain_bias(_chain("BALANCED", oi_chg_pe=90000, oi_chg_ce=1000), 100.0, "CE", _G)
    assert b["doi_side"] == "PUT_WRITE" and b["verdict"] == "CONFIRM"


def test_chain_bias_neutral_when_balanced():
    b = ss._chain_bias(_chain("BALANCED"), 100.0, "CE", _G)
    assert b["verdict"] == "NEUTRAL"


def test_chain_bias_insufficient_on_thin_coverage_or_thin_chain():
    assert ss._chain_bias(_chain("PUT_HEAVY", cover=False), 100.0, "CE", _G)["verdict"] == "INSUFFICIENT"
    assert ss._chain_bias([{"strike": 100.0}], 100.0, "CE", _G)["verdict"] == "INSUFFICIENT"
    assert ss._chain_bias(None, 100.0, "CE", _G)["verdict"] == "INSUFFICIENT"


def test_chain_bias_never_crashes_on_malformed_rows():
    bad = [{"strike": 100.0, "ce": None, "pe": None},
           {"strike": 101.0, "ce": {"oi": None}, "pe": {"oi": "x"}},
           {"garbage": 1}]
    out = ss._chain_bias(bad, 100.0, "CE", _G)
    assert out["verdict"] in ("INSUFFICIENT", "NEUTRAL")


def test_chain_bias_is_pure_deterministic():
    c = _chain("PUT_HEAVY")
    assert ss._chain_bias(c, 100.0, "CE", _G) == ss._chain_bias(c, 100.0, "CE", _G)


# --------------------------------------------------------------------------- #
#  gate inside decide_from_context -- upstream engines stubbed to a qualified  #
#  BUY_CE (bullish), then the chain is varied                                  #
# --------------------------------------------------------------------------- #
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


def _bars():
    return {"5m": [[0, 100, 101, 99, 100, 1000]] * 30}


def _run(monkeypatch, chain, cfg, *, state_score=72.0):
    _stub_qualified_buy(monkeypatch, state_score=state_score)
    return ss.decide_from_context(_bars(), chain, atm=100.0, calib=None, config=cfg)


_PLAN_KEYS = ("entry", "stop_loss", "target_1", "target_2", "probability", "ev", "ev_r", "rr")


def test_gate_off_is_a_noop(monkeypatch):
    ch = _chain("CALL_HEAVY")                              # would contradict a bullish setup
    base = _run(monkeypatch, ch, {"filters": {}})          # no chain_gates key
    off = _run(monkeypatch, ch, {"filters": {}, "chain_gates": {"enabled": False}})
    assert base["decision"] == off["decision"] == "BUY_CE"
    assert off.get("chain_bias") is None
    assert {k: base.get(k) for k in _PLAN_KEYS} == {k: off.get(k) for k in _PLAN_KEYS}


def test_gate_on_confirm_keeps_buy_and_attaches_bias(monkeypatch):
    d = _run(monkeypatch, _chain("PUT_HEAVY"), {"filters": {}, "chain_gates": {"enabled": True}})
    assert d["decision"] == "BUY_CE"
    assert d["chain_bias"]["verdict"] == "CONFIRM"
    d_off = _run(monkeypatch, _chain("PUT_HEAVY"), {"filters": {}})
    assert {k: d.get(k) for k in _PLAN_KEYS} == {k: d_off.get(k) for k in _PLAN_KEYS}  # plan untouched


def test_gate_on_confirm_can_bump_confidence_when_opted_in(monkeypatch):
    base = _run(monkeypatch, _chain("PUT_HEAVY"), {"filters": {}})
    up = _run(monkeypatch, _chain("PUT_HEAVY"),
              {"filters": {}, "chain_gates": {"enabled": True, "confirm_bumps_confidence": True}})
    order = ["LOW", "MEDIUM", "HIGH"]
    assert order.index(up["confidence"]) >= order.index(base["confidence"])
    assert {k: base.get(k) for k in _PLAN_KEYS} == {k: up.get(k) for k in _PLAN_KEYS}


def test_gate_on_contradiction_vetoes_a_marginal_setup(monkeypatch):
    # marginal: force the score below the veto threshold via config
    d = _run(monkeypatch, _chain("CALL_HEAVY"),
             {"filters": {}, "chain_gates": {"enabled": True, "marginal_score": 95.0}})
    assert d["decision"] == "NO_TRADE"
    assert "option-chain bias contradicts" in d["reason"]
    assert d["chain_bias"]["verdict"] == "CONTRADICT"


def test_gate_on_contradiction_only_haircuts_a_strong_setup(monkeypatch):
    # strong: default marginal_score 55 ; blended ~75 -> not vetoed, confidence -1
    base = _run(monkeypatch, _chain("CALL_HEAVY"), {"filters": {}})
    d = _run(monkeypatch, _chain("CALL_HEAVY"), {"filters": {}, "chain_gates": {"enabled": True}})
    assert d["decision"] in ("BUY_CE", "WATCH")            # kept (or watch if it was already LOW)
    order = ["LOW", "MEDIUM", "HIGH"]
    assert order.index(d["confidence"]) <= order.index(base["confidence"])
    if d["decision"] == "BUY_CE":
        assert {k: base.get(k) for k in _PLAN_KEYS} == {k: d.get(k) for k in _PLAN_KEYS}


def test_gate_on_insufficient_chain_is_a_noop(monkeypatch):
    base = _run(monkeypatch, _chain("CALL_HEAVY", cover=False), {"filters": {}})
    d = _run(monkeypatch, _chain("CALL_HEAVY", cover=False),
             {"filters": {}, "chain_gates": {"enabled": True, "marginal_score": 95.0}})
    assert d["decision"] == base["decision"] == "BUY_CE"   # thin OI -> gate did nothing
    assert d["chain_bias"]["verdict"] == "INSUFFICIENT"


def test_gate_reads_only_the_passed_chain_no_leakage(monkeypatch):
    """The gate must be a pure function of (chain at T). Two identical calls with
    the same chain give the same verdict; a different chain gives a different one
    -- nothing time/global-dependent leaks in."""
    a = _run(monkeypatch, _chain("PUT_HEAVY"), {"filters": {}, "chain_gates": {"enabled": True}})
    b = _run(monkeypatch, _chain("PUT_HEAVY"), {"filters": {}, "chain_gates": {"enabled": True}})
    c = _run(monkeypatch, _chain("CALL_HEAVY"), {"filters": {}, "chain_gates": {"enabled": True}})
    assert a["chain_bias"] == b["chain_bias"]
    assert a["chain_bias"]["verdict"] != c["chain_bias"]["verdict"]
