"""
H1/H7 Structural State Engine -- SHADOW / OBSERVATION MODE.

Deterministic unit tests for every classifier rule the brief calls out:
  * H1 gate is disp_atr >= 1.0 (Stage-7 refined) -- NOT body_frac
  * body_fraction is informational only
  * every H7 state maps to AVOID
  * AMBIGUOUS maps to NO_ACTION
  * available_R < 1.0 (or unobservable) maps to NO_ACTION
  * NIFTY / NATURALGAS H1 continuation stays NOT_VALIDATED
  * CRUDEOIL H1 continuation stays research-only (never PROVEN)
  * no look-ahead (classify(idx) == classify(idx) on bars truncated to idx+4)
  * no option-premium inference, no BUY/SELL/ORDER semantics
  * unobservable order-flow features remain explicitly unobservable
"""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.orderflow import h1h7_state as H   # noqa: E402

ALLOWED_ACTIONS = {"AVOID", "NO_ACTION", "CONTINUATION_CANDIDATE"}


# ---------------------------------------------------------------- ctx helper
def _ctx(**over):
    """A baseline context that -- unmodified -- classifies as H1_CONT for
    CRUDEOIL (full Stage-7 gate satisfied)."""
    c = dict(
        symbol="CRUDEOIL", timestamp="2026-09-04T06:00:00Z", is_spike=True,
        direction="LONG", spike_range=10.0, range_pctile=0.95, range_x=5.0,
        atr=2.0, disp_atr=1.5, body_fraction=0.60,
        broken_level=100.0, broken_level_kind="prior_bar_high",
        acc1=True, acc2=True, n1_agreement=True,
        reclaimed_within_3=False, reclaim_distance=0.0, reclaim_distance_ratio=0.0,
        available_R=3.0,
    )
    c.update(over)
    return c


# ================================================================ H7 -> AVOID
@pytest.mark.parametrize("ratio,expect_state", [
    (0.29, "H7_LEANING_TRAP"),
    (0.60001, "H7_TRAP"),
    (1.50, "H7_TRAP"),
    (5.00, "H7_TRAP"),
])
def test_h7_states_always_map_to_avoid(ratio, expect_state):
    out = H.classify_market_state(_ctx(reclaimed_within_3=True,
                                      reclaim_distance_ratio=ratio,
                                      reclaim_distance=ratio * 10.0))
    assert out["state"] == expect_state
    assert out["action"] == "AVOID"
    assert out["research_status"] == "SUPPORTED"
    # AVOID is not a fade -- no short/opposite semantics leak in
    assert "fade" not in out["reason"].lower() or "REJECTED" in out["reason"]


def test_classify_h7_trap_band_boundaries():
    assert H.classify_h7_trap(0.0, False) == "NONE"
    assert H.classify_h7_trap(0.20, True) == "AMBIGUOUS_SHALLOW"
    assert H.classify_h7_trap(0.28, True) == "AMBIGUOUS_SHALLOW"      # <= shallow
    assert H.classify_h7_trap(0.2801, True) == "H7_LEANING_TRAP"
    assert H.classify_h7_trap(0.60, True) == "H7_LEANING_TRAP"         # knee not yet crossed
    assert H.classify_h7_trap(0.6001, True) == "H7_TRAP"
    assert H.classify_h7_trap(9.9, False) == "NONE"                    # no reclaim -> not a trap


# ================================================================ AMBIGUOUS -> NO_ACTION
def test_shallow_reclaim_is_ambiguous_no_action():
    out = H.classify_market_state(_ctx(reclaimed_within_3=True,
                                      reclaim_distance_ratio=0.20,
                                      reclaim_distance=2.0))
    assert out["state"] == "AMBIGUOUS"
    assert out["action"] == "NO_ACTION"


def test_else_branch_is_ambiguous_no_action():
    # no reclaim, but the H1 acceptance gate is not met either
    out = H.classify_market_state(_ctx(acc1=False, acc2=False, n1_agreement=False))
    assert out["state"] == "AMBIGUOUS"
    assert out["action"] == "NO_ACTION"


# ================================================================ H1 gate = disp_atr >= 1.0
@pytest.mark.parametrize("disp_atr,is_h1", [
    (0.0, False), (0.50, False), (0.999, False), (1.0, True), (1.5, True), (4.0, True),
])
def test_h1_gate_uses_disp_atr_threshold(disp_atr, is_h1):
    out = H.classify_market_state(_ctx(disp_atr=disp_atr))
    if is_h1:
        assert out["state"] == "H1_CONT"
        assert out["action"] == "CONTINUATION_CANDIDATE"
    else:
        # gate fails on displacement -> falls through to H1_WEAK (acc1 & n1) / no continuation
        assert out["state"] in ("H1_WEAK", "AMBIGUOUS")
        assert out["action"] == "NO_ACTION"


def test_classify_h1_continuation_pure():
    assert H.classify_h1_continuation(True, True, 1.0, 2.0) == "H1_CONT"
    assert H.classify_h1_continuation(True, True, 0.99, 2.0) == "NO_H1"     # disp_atr gate
    assert H.classify_h1_continuation(False, True, 2.0, 2.0) == "NO_H1"     # needs acc2
    assert H.classify_h1_continuation(True, False, 2.0, 2.0) == "NO_H1"     # needs n1_agree
    assert H.classify_h1_continuation(True, True, 2.0, 0.9) == "H1_CONT_BLOCKED"
    assert H.classify_h1_continuation(True, True, 2.0, None) == "H1_CONT_BLOCKED"


# ================================================================ body_fraction is display-only
@pytest.mark.parametrize("bf", [0.05, 0.20, 0.40, 0.5499, 0.55, 0.95])
def test_body_fraction_never_gates_the_state(bf):
    """Changing body_fraction across the old 0.55 threshold must not move the
    state -- disp_atr is the gate now (Stage-7)."""
    out = H.classify_market_state(_ctx(body_fraction=bf, disp_atr=1.5))
    assert out["state"] == "H1_CONT"           # unchanged regardless of body_fraction
    assert out["body_fraction"] == bf
    assert "informational only" in out["body_fraction_note"].lower()
    assert "not a" in out["body_fraction_note"].lower()


def test_body_fraction_low_still_h1_when_disp_atr_passes():
    weak_body = H.classify_market_state(_ctx(body_fraction=0.10, disp_atr=1.2))
    strong_body = H.classify_market_state(_ctx(body_fraction=0.90, disp_atr=1.2))
    assert weak_body["state"] == strong_body["state"] == "H1_CONT"


# ================================================================ available_R gate
def test_available_r_below_one_blocks_to_no_action():
    out = H.classify_market_state(_ctx(available_R=0.7))
    assert out["state"] == "H1_CONT_BLOCKED"
    assert out["action"] == "NO_ACTION"
    assert "available_R" in out["reason"] and "1.0" in out["reason"]


def test_available_r_none_is_unobservable_not_fabricated():
    out = H.classify_market_state(_ctx(available_R=None))
    assert out["state"] == "H1_CONT_BLOCKED"
    assert out["action"] == "NO_ACTION"
    assert "UNOBSERVABLE" in out["reason"]
    assert "not fabricated" in out["reason"].lower()


# ================================================================ per-symbol research status
@pytest.mark.parametrize("sym", ["NIFTY", "NATURALGAS"])
def test_nifty_natgas_h1_continuation_stays_not_validated(sym):
    out = H.classify_market_state(_ctx(symbol=sym))
    assert out["state"] == "H1_CONT_OBSERVE"
    assert out["action"] == "NO_ACTION"
    assert out["research_status"] == "NOT_VALIDATED"


def test_crudeoil_h1_continuation_is_research_only_never_proven():
    out = H.classify_market_state(_ctx(symbol="CRUDEOIL"))
    assert out["state"] == "H1_CONT"
    assert out["action"] == "CONTINUATION_CANDIDATE"
    assert out["research_status"] == "RESEARCH_ONLY_PROMISING"
    assert "PROVEN" not in out["research_status"]
    assert "research" in out["reason"].lower()
    assert "not proven" in out["reason"].lower()


def test_research_status_legend_has_no_proven_edge():
    assert "PROVEN" in H.RESEARCH_STATUS_LEGEND
    assert "not used" in H.RESEARCH_STATUS_LEGEND["PROVEN"].lower()
    # no legend entry claims a proven / guaranteed / profitable edge
    for v in H.RESEARCH_STATUS_LEGEND.values():
        assert "guaranteed" not in v.lower()
        assert "profitable" not in v.lower()


# ================================================================ restricted action set
def test_only_three_actions_can_ever_be_emitted():
    ctxs = [
        _ctx(), _ctx(symbol="NIFTY"), _ctx(symbol="NATURALGAS"),
        _ctx(is_spike=False), _ctx(broken_level=None),
        _ctx(reclaimed_within_3=True, reclaim_distance_ratio=0.2),
        _ctx(reclaimed_within_3=True, reclaim_distance_ratio=0.4),
        _ctx(reclaimed_within_3=True, reclaim_distance_ratio=0.9),
        _ctx(available_R=0.5), _ctx(available_R=None),
        _ctx(disp_atr=0.3), _ctx(acc1=False, acc2=False, n1_agreement=False),
        _ctx(acc2=False),
    ]
    for c in ctxs:
        assert H.classify_market_state(c)["action"] in ALLOWED_ACTIONS
    for a in H._ACTION.values():
        assert a in ALLOWED_ACTIONS


def test_no_spike_is_neutral_no_action():
    out = H.classify_market_state(_ctx(is_spike=False))
    assert out["state"] == "NEUTRAL"
    assert out["action"] == "NO_ACTION"


def test_spike_without_level_is_no_action():
    out = H.classify_market_state(_ctx(broken_level=None))
    assert out["state"] == "SPIKE_NO_LEVEL"
    assert out["action"] == "NO_ACTION"


# ================================================================ no premium / no order semantics
def test_no_option_premium_or_order_semantics_in_output():
    out = H.classify_market_state(_ctx())
    banned = ("premium", "strike", "option_type", "ce", "pe", "buy", "sell",
              "order", "qty", "lots")
    for k in out:
        assert k.lower() not in banned, f"engine output leaked field {k!r}"


def test_engine_module_does_not_import_premium_or_execution():
    src = (Path(H.__file__)).read_text()
    assert "premium_walk" not in src
    assert "session_option_quotes" not in src
    assert "from ..execution" not in src and "import execution" not in src
    # no live-order / broker plumbing
    for tok in ("place_order", "broker", "OrderManager", "auto_trade", "live_trading = True"):
        assert tok not in src


def test_engine_never_emits_a_directional_trade_instruction():
    # states / actions must not carry BUY / SELL / LONG-entry / SHORT-entry verbs
    for c in (_ctx(), _ctx(direction="SHORT"), _ctx(symbol="NIFTY")):
        out = H.classify_market_state(c)
        assert out["state"] not in ("BUY", "SELL")
        assert out["action"] not in ("BUY", "SELL", "ENTER_LONG", "ENTER_SHORT", "PLACE_ORDER")
        # spike_direction is a description of the bar, not an instruction
        assert out["spike_direction"] in ("LONG", "SHORT", None)


# ================================================================ unobservable stays unobservable
def test_every_state_carries_the_unobservable_marker():
    for c in (_ctx(), _ctx(is_spike=False), _ctx(reclaimed_within_3=True,
                                                 reclaim_distance_ratio=1.0)):
        out = H.classify_market_state(c)
        assert out["unobservable_orderflow"] == [
            "aggressor_volume", "trade_delta", "bid_ask_imbalance",
            "depth_imbalance", "absorption", "footprint_concentration",
            "iceberg_liquidity", "true_constituent_causation",
        ]
        assert "DATA NOT AVAILABLE" in out["unobservable_note"]
        # none of the unobservable names appear as a real (numeric) field
        for name in out["unobservable_orderflow"]:
            assert name not in out


# ================================================================ synthetic session plumbing
def _bar(bs, o, h, l, c):
    return {"bar_start": bs, "o": o, "h": h, "l": l, "c": c, "v": 0.0}


def _session(spike_follow, *, direction="LONG", spike_body_open=101.0,
             sess_hi_bar_high=300.0):
    """~24 calm bars + 1 early tall bar (sets a far session high / swing high) +
    an abnormal LONG spike that breaks the prior bar high, then 4 follow bars
    whose closes are given by `spike_follow` (len 4). The spike is idx=25."""
    bars = []
    t0 = 1_000_000
    def ts(i):
        return f"2026-09-04T{4 + i // 12:02d}:{(i % 12) * 5:02d}:00Z"
    # early tall bar -> session high & a swing high well above everything
    bars.append(_bar(ts(0), 100.0, sess_hi_bar_high, 99.0, 100.5))
    # calm bars, tiny ranges, drifting ~100..103
    for i in range(1, 24):
        base = 100.0 + (i % 3) * 0.5
        bars.append(_bar(ts(i), base, base + 1.0, base - 1.0, base + 0.3))
    # prior bar to the spike: high = 103 -> this becomes L
    bars.append(_bar(ts(24), 101.5, 103.0, 101.0, 102.0))
    # the abnormal spike (idx 25): big range, closes above L=103, below sess hi
    if direction == "LONG":
        bars.append(_bar(ts(25), spike_body_open, 118.0, 101.0, 112.0))
    else:
        bars.append(_bar(ts(25), 201.0 - spike_body_open + 100, 199.0, 82.0, 88.0))
    # follow bars
    for k, cl in enumerate(spike_follow, start=26):
        bars.append(_bar(ts(k), cl, cl + 1.5, cl - 1.5, cl))
    # a couple of trailing calm bars so idx 25 is well inside range
    for k in range(26 + len(spike_follow), 26 + len(spike_follow) + 3):
        bars.append(_bar(ts(k), 110.0, 111.0, 109.0, 110.0))
    return bars, 25


def test_synthetic_session_produces_h1_cont_for_crudeoil():
    bars, idx = _session([108.0, 109.0, 110.0, 110.5])   # all closes hold above L=103
    res = H.classify_session(bars, "CRUDEOIL")
    ev = [e for e in res["events"] if e["timestamp"] == bars[idx]["bar_start"]]
    assert len(ev) == 1
    e = ev[0]
    assert e["state"] == "H1_CONT"
    assert e["action"] == "CONTINUATION_CANDIDATE"
    assert e["disp_atr"] is not None and e["disp_atr"] >= 1.0
    assert e["body_fraction"] is not None          # present but not a gate
    assert e["available_R"] is not None and e["available_R"] >= 1.0
    assert res["live_trading"] is False and res["mode"] == "SHADOW_OBSERVATION"


def test_synthetic_same_session_nifty_is_observe_only():
    bars, idx = _session([108.0, 109.0, 110.0, 110.5])
    e = [x for x in H.classify_session(bars, "NIFTY")["events"]
         if x["timestamp"] == bars[idx]["bar_start"]][0]
    assert e["state"] == "H1_CONT_OBSERVE"
    assert e["action"] == "NO_ACTION"
    assert e["research_status"] == "NOT_VALIDATED"


def test_synthetic_session_h7_trap_when_price_reclaims_deep():
    # follow bar closes far back below L=103 -> deep reclaim -> H7_TRAP
    bars, idx = _session([90.0, 95.0, 100.0, 101.0])
    e = [x for x in H.classify_session(bars, "CRUDEOIL")["events"]
         if x["timestamp"] == bars[idx]["bar_start"]][0]
    assert e["state"] in ("H7_TRAP", "H7_LEANING_TRAP")
    assert e["action"] == "AVOID"


# ================================================================ no look-ahead
def test_no_lookahead_full_vs_truncated_identical():
    bars, idx = _session([108.0, 109.0, 110.0, 110.5])
    full = H.classify_session(bars, "CRUDEOIL")
    # truncate so only T+1..T+3 (idx+1..idx+3) are present -- exactly what the
    # engine is allowed to use. idx+4 and beyond removed.
    trunc = H.classify_session(bars[:idx + 4], "CRUDEOIL")
    ef = [e for e in full["events"] if e["timestamp"] == bars[idx]["bar_start"]][0]
    et = [e for e in trunc["events"] if e["timestamp"] == bars[idx]["bar_start"]][0]
    assert ef == et


def test_event_not_emitted_until_three_forward_bars_complete():
    bars, idx = _session([108.0, 109.0, 110.0, 110.5])
    # only idx+1 and idx+2 present -> spike bar is NOT yet eligible
    too_early = H.classify_session(bars[:idx + 3], "CRUDEOIL")
    assert all(e["timestamp"] != bars[idx]["bar_start"] for e in too_early["events"])
    # idx+3 present -> eligible
    ok = H.classify_session(bars[:idx + 4], "CRUDEOIL")
    assert any(e["timestamp"] == bars[idx]["bar_start"] for e in ok["events"])


def test_every_event_from_a_session_is_shadow_and_not_live():
    bars, _ = _session([108.0, 109.0, 110.0, 110.5])
    res = H.classify_session(bars, "CRUDEOIL")
    for e in res["events"]:
        assert e["live_trading"] is False
        assert e["mode"] == "SHADOW_OBSERVATION"
        assert e["action"] in ALLOWED_ACTIONS
        assert "unobservable_orderflow" in e


# ================================================================ shadow CSV
def test_shadow_csv_appends_then_dedups(tmp_path, monkeypatch):
    p = tmp_path / "shadow.csv"
    monkeypatch.setenv("ORDERFLOW_H1H7_SHADOW_CSV", str(p))
    bars, _ = _session([108.0, 109.0, 110.0, 110.5])
    res = H.classify_session(bars, "CRUDEOIL")
    events = res["events"]
    assert events
    r1 = H.append_shadow_rows(events)
    assert r1["appended"] == len(events)
    r2 = H.append_shadow_rows(events)               # same events -> nothing new
    assert r2["appended"] == 0
    with p.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(events)
    # flattened, no premium columns, unobservable list serialised
    assert "premium" not in ",".join(rows[0].keys()).lower()
    assert "|" in rows[0]["unobservable_orderflow"]
    assert rows[0]["live_trading"] in ("False", "false", "0")


def test_shadow_path_default_is_under_backend_data():
    monkeypatch_free = H.shadow_log_path()
    assert monkeypatch_free.name == "orderflow_h1h7_shadow.csv"
    assert monkeypatch_free.parent.name == "data"
