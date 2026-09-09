"""
ORDERFLOW_ENGINE v1 -- offline unit tests. No network.

Covers: capability honesty (no fabricated order flow), indicator causality,
RK-score bounds + capability-aware renorm, the two-stage entry state machine
(SIGNAL != ENTRY), no-chase filter, RR rejection, entry-quality bounds,
calibration determinism + TRAIN-only selection, exit-engine R consistency,
option layer separation, and no live-app imports.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.research_engines.orderflow import indicators as I          # noqa: E402
from app.research_engines.orderflow import orderflow as OF          # noqa: E402
from app.research_engines.orderflow import signal as SG             # noqa: E402
from app.research_engines.orderflow import entry_timing as ET       # noqa: E402
from app.research_engines.orderflow import exit_engine as EX        # noqa: E402
from app.research_engines.orderflow import calibrate as CAL         # noqa: E402
from app.research_engines.orderflow import option_map as OM         # noqa: E402
from app.research_engines.orderflow import data as DATA             # noqa: E402
from app.research_engines.orderflow.config import merged            # noqa: E402

CFG = merged()


def _bars(n=300, seed_trend=1.0):
    out = []
    px = 20000.0
    for i in range(n):
        px += seed_trend * (2.0 + (i % 9 - 4) * 1.2)
        o = px - seed_trend * 1.5
        h = max(o, px) + 3.0
        l = min(o, px) - 3.0
        c = px
        m = 9 * 60 + 15 + (i % 60)
        out.append({"t": i * 300, "o": o, "h": h, "l": l, "c": c, "v": 0.0, "n": 1,
                    "session_date": f"2024-{(i // 60) % 12 + 1:02d}-{(i // 5) % 27 + 1:02d}",
                    "hhmm": f"{m // 60:02d}:{m % 60:02d}", "minute_of_day": m,
                    "hlc3": (h + l + c) / 3.0, "source": "synth"})
    return out


# ---- data / capability honesty ----

def test_capability_declares_true_orderflow_false():
    bars, cap = DATA.load("NIFTY", tf_min=5, start="2015-01-01", end="2015-03-01")
    assert cap["true_orderflow"] is False
    assert cap["has_aggressor"] is False and cap["has_trades"] is False
    assert cap["volume_available"] is False
    assert "TRUE ORDER FLOW NOT AVAILABLE" in cap["note"]


def test_frame_labels_flow_as_proxy_never_delta():
    frame = OF.build(_bars(), CFG)
    r = frame[-1]
    assert r["method"]["flow"] == "PRESSURE_PROXY"
    assert r["method"]["cvd"] == "CUM_PRESSURE_PROXY"
    assert "delta" not in r and "cvd" not in r          # only *_proxy keys exist
    assert -1.0 <= r["flow_proxy"] <= 1.0


# ---- indicators ----

def test_indicator_causality_and_bounds():
    x = [10.0 + (i % 5) for i in range(80)]
    e = I.ema(x, 20)
    assert len(e) == len(x) and e[0] == x[0]
    rs = I.rsi(x, 14)
    assert all(v is None or 0.0 <= v <= 100.0 for v in rs)
    h = [v + 1 for v in x]
    l = [v - 1 for v in x]
    ad = I.adx(h, l, x, 14)
    assert all(v is None or 0.0 <= v <= 100.0 for v in ad)
    # pivots are confirmed only w bars later
    hi, lo = I.pivots(h, l, 3)
    assert all(3 <= p < len(x) - 3 for p in hi + lo)


# ---- RK score ----

def test_rk_score_bounds_and_direction():
    frame = OF.build(_bars(seed_trend=1.0), CFG)
    r = SG.rk_score(frame[-1], CFG)
    assert 0.0 <= r["rk_score"] <= 100.0
    assert 0.0 <= r["rk_tilt_score"] <= 100.0
    assert -1.0 <= r["tilt"] <= 1.0
    assert r["direction"] in ("LONG", "SHORT", "NONE")
    assert 0.0 <= r["coverage"] <= 1.0
    down = SG.rk_score(OF.build(_bars(seed_trend=-1.0), CFG)[-1], CFG)
    # opposite trend -> opposite or NONE, never the same non-NONE dir
    assert not (r["direction"] == "LONG" and down["direction"] == "LONG")


# ---- two-stage entry: SIGNAL != ENTRY ----

def test_entry_is_two_stage_and_needs_a_pullback():
    bars = _bars(seed_trend=1.0)
    frame = OF.build(bars, CFG)
    # a strictly rising series never pulls back -> no ENTRY_READY, must EXPIRE
    res = ET.run(bars, frame, 120, "LONG", 80.0, merged({"entry_zone_frac": 0.3}))
    assert res["status"] != "ENTRY_READY"
    assert res["status"].startswith("EXPIRED")
    # and it always reports the signal geometry
    assert res["signal_range"] == res["signal_high"] - res["signal_low"]


def test_nochase_and_rr_rejections_exist():
    bars = _bars(seed_trend=1.0)
    frame = OF.build(bars, CFG)
    # brutal no-chase config -> any fill rejected as extended
    tight = merged({"entry_zone_frac": 0.0, "max_entry_extension_atr": 0.01,
                    "entry_require_momentum_recovery": False, "invalidate_on_origin_break": False})
    res = ET.run(bars, frame, 100, "LONG", 90.0, tight)
    assert res["status"] in ("EXPIRED_NOCHASE", "EXPIRED_TIMEOUT", "EXPIRED_INVALIDATED")


def test_entry_ready_geometry_and_r_consistency_on_real_data():
    """Real NIFTY slice through the whole pipeline: some signals must reach
    ENTRY_READY, and every ENTRY_READY must have sane geometry + R math."""
    bars, cap = DATA.load("NIFTY", tf_min=5, start="2019-01-01", end="2020-06-30")
    if cap["n_sessions"] < 60:
        import pytest
        pytest.skip("history slice unavailable in this environment")
    frame = OF.build(bars, CFG)
    sigs = [s for s in SG.detect(frame, CFG) if s["fresh_signal"]]
    ready = 0
    for s in sigs:
        r = ET.run(bars, frame, s["i"], s["direction"], s["rk_score"], CFG, zone_frac=0.3)
        assert r["status"] in ("ENTRY_READY", "EXPIRED_TIMEOUT", "EXPIRED_NOCHASE",
                               "EXPIRED_INVALIDATED")
        if r["status"] != "ENTRY_READY":
            continue
        ready += 1
        assert r["fill_index"] > r["signal_index"]          # two-stage: entry is later
        assert 0.0 <= r["entry_quality"] <= 100.0
        assert r["rr"] >= CFG["min_rr"]
        if r["direction"] == "LONG":
            assert r["stop_loss"] < r["entry"] < r["t1"] < r["t2"] < r["t3"]
        else:
            assert r["stop_loss"] > r["entry"] > r["t1"] > r["t2"] > r["t3"]
        ex = EX.simulate(bars, r, CFG)
        assert abs(ex["r_multiple"] - ex["points"] / r["risk_points"]) < 5e-3
        assert ex["win"] == (ex["points"] > 0)
    assert ready > 0, "the pipeline must produce at least one confirmed entry on real data"


# ---- calibration ----

def test_calibration_is_deterministic_and_train_only():
    bars = _bars(seed_trend=0.6)
    frame = OF.build(bars, CFG)
    sigs = [s for s in SG.detect(frame, CFG) if s["fresh_signal"]]
    dates = sorted({b["session_date"] for b in bars})
    tr, va = dates[: int(len(dates) * 0.6)], dates[int(len(dates) * 0.6): int(len(dates) * 0.8)]
    a = CAL.calibrate(bars, frame, sigs, merged({"min_trades_per_zone": 1}), tr, va)
    b = CAL.calibrate(bars, frame, sigs, merged({"min_trades_per_zone": 1}), tr, va)
    assert a["chosen_zone"] == b["chosen_zone"]
    assert a["train_table"].keys() == set(CFG["entry_zones"])
    assert a["chosen_zone"] in CFG["entry_zones"] or a["chosen_zone"] == CFG["entry_zone_frac"]


# ---- option layer separation ----

def test_option_layer_is_advisory_and_downstream_only():
    setup = {"direction": "LONG", "entry": 20000.0, "stop_loss": 19950.0,
             "t1": 20050.0, "t2": 20090.0, "t3": 20150.0}
    om = OM.map_to_option(setup, spot=20000.0, strike_step=50.0)
    assert om["option_type"] == "CE"
    assert om["layer"] == "OPTION_ADVISORY"
    assert om["approx_premium_risk"] > 0 and om["approx_premium_reward_t1"] > 0
    # inverse
    assert OM.map_to_option({**setup, "direction": "SHORT"}, spot=20000.0)["option_type"] == "PE"


# ---- isolation ----

def test_no_live_app_imports():
    pkg = Path(__file__).parents[1] / "app" / "research_engines" / "orderflow"
    banned = ("app.autoscalp", "app.execution", "app.engines", "app.hcs",
              "app.connectors", "app.main")
    for py in pkg.glob("*.py"):
        tree = ast.parse(py.read_text())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
            elif isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
        hits = [m for m in mods if any(m == b or m.startswith(b + ".") for b in banned)]
        assert not hits, f"{py.name}: {hits}"
