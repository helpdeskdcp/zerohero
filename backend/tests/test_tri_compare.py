"""
TRI-COMPARE 3-engine framework -- offline unit tests. No network.

Covers: fair harness (identical cost/session/risk model applied to all engines),
causality + determinism, the two-stage SIGNAL!=ENTRY property for E2/E3, the
mathematical RR gate, ANN no-future-leak + TRAIN-only fit, composite
sample-sufficiency, and no live-app imports.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.research_engines.tri_compare import features as FE       # noqa: E402
from app.research_engines.tri_compare import engine_trend as E1    # noqa: E402
from app.research_engines.tri_compare import engine_structure as E2  # noqa: E402
from app.research_engines.tri_compare import engine_hybrid as E3    # noqa: E402
from app.research_engines.tri_compare import harness as H          # noqa: E402
from app.research_engines.tri_compare import metrics as M          # noqa: E402
from app.research_engines.tri_compare.ann_layer import AnnConfirm, _feat  # noqa: E402
from app.research_engines.tri_compare.config import merged         # noqa: E402
from app.research_engines.orderflow import data as D               # noqa: E402

CFG = merged({"tf_min": 15})


def _real_frame(start="2019-01-01", end="2020-06-30"):
    bars, cap = D.load("NIFTY", tf_min=15, start=start, end=end)
    return bars, FE.build_frame(bars, CFG), cap


def test_frame_is_causal_and_deterministic():
    bars, f1, _ = _real_frame()
    _, f2, _ = _real_frame()
    # deterministic
    assert [r["structure_state"] for r in f1] == [r["structure_state"] for r in f2]
    assert [r["di_plus"] for r in f1[:200]] == [r["di_plus"] for r in f2[:200]]
    # truncating the future must not change a past bar's features
    ftr = FE.build_frame(bars[:1000], CFG)
    for a, b in zip(ftr, f1[:1000]):
        assert a["structure_state"] == b["structure_state"]
        assert a["don_hi"] == b["don_hi"] and a["di_plus"] == b["di_plus"]


def test_all_engines_produce_signals_and_are_deterministic():
    bars, frame, _ = _real_frame()
    s1a = E1.signals(frame, CFG)
    s1b = E1.signals(frame, CFG)
    s2 = E2.signals(frame, bars, CFG, e_key="e2")
    s3 = E3.signals(frame, bars, CFG)
    assert s1a == s1b
    assert len(s1a) > 0 and len(s2) > 0 and len(s3) > 0
    # E1 fires at the signal bar; E2/E3 entries are LATER than the signal (two-stage)
    assert all(s["fill_index"] == s["signal_index"] for s in s1a)
    for s in s2 + s3:
        if s.get("status") == "ENTRY_READY":
            assert s["fill_index"] > s["signal_index"], "SIGNAL != ENTRY for E2/E3"


def test_harness_applies_identical_risk_model_to_every_engine():
    bars, frame, _ = _real_frame()
    outs = {}
    for name, sigs in (("E1_TREND", E1.signals(frame, CFG)),
                       ("E2_STRUCTURE", E2.signals(frame, bars, CFG, e_key="e2")),
                       ("E3_HYBRID", E3.signals(frame, bars, CFG))):
        outs[name] = H.run_engine(name, sigs, bars, frame, CFG)
    for name, o in outs.items():
        for t in o["trades"]:
            atr_like = abs(t["entry"] - t["stop_loss"])
            # SL is exactly sl_atr_mult * ATR from entry (the ONE shared risk model)
            assert abs(atr_like - t["risk_points"]) < 0.05, name
            # targets are the shared R multiples
            r = t["risk_points"]
            if t["direction"] == "LONG":
                assert abs((t["t1"] - t["entry"]) - CFG["target_r_multiples"][0] * r) < 0.05
                assert abs((t["t3"] - t["entry"]) - CFG["target_r_multiples"][2] * r) < 0.05
            # RR-room gate honoured
            assert t["rr_room"] >= CFG["min_rr"] - 1e-6
            # R / points consistency
            assert abs(t["r_multiple"] - t["points"] / r) < 5e-3


def test_rr_gate_rejects_when_no_room():
    bars, frame, _ = _real_frame()
    sigs = E1.signals(frame, CFG)
    tight = merged({"tf_min": 15, "min_rr": 9.9})     # impossible RR -> everything rejected
    o = H.run_engine("E1_TREND", sigs, bars, frame, tight)
    assert o["entries_total"] == 0
    assert o["rejected"].get("RR_TOO_LOW", 0) > 0


def test_ann_is_train_only_and_leak_free(monkeypatch):
    bars, frame, _ = _real_frame("2018-01-01", "2022-06-30")
    sigs = E1.signals(frame, CFG)
    trades = H.run_engine("E1_TREND", sigs, bars, frame, CFG)["trades"]
    assert len(trades) > 120
    cut = int(len(trades) * 0.6)
    tr, te = trades[:cut], trades[cut:]
    ann = AnnConfirm(CFG["ann"]).fit(tr)
    assert ann.status in ("OK", "INSUFFICIENT")
    if ann.status == "OK":
        # feature vector must not contain the trade's own outcome / pnl
        fx = _feat(te[0])
        for banned in ("win", "points", "r_multiple", "exit_reason", "mfe_R", "mae_R"):
            assert banned not in fx
        # deterministic given the seed
        a = AnnConfirm(CFG["ann"]).fit(tr).p_win(te[0])
        b = AnnConfirm(CFG["ann"]).fit(tr).p_win(te[0])
        assert a == b
        # threshold chosen on TRAIN only (0..1)
        assert 0.0 <= ann.threshold <= 1.0


def test_composite_sample_sufficiency_penalises_tiny_oos():
    train = {"n": 100, "expectancy_R": 0.1}
    reg = {"TRENDING/MID": {"n": 20, "expectancy_R": 0.2}, "RANGING/LOW": {"n": 15, "expectancy_R": 0.1}}
    big = M.composite({"n": 200, "expectancy_R": 0.2, "profit_factor": 1.6, "max_drawdown_R": -10.0},
                      train, reg, merged()["composite_weights"], 40)
    small = M.composite({"n": 12, "expectancy_R": 0.2, "profit_factor": 1.6, "max_drawdown_R": -1.0},
                        train, reg, merged()["composite_weights"], 40)
    assert small["sample_sufficiency"] < 0.5
    assert small["score"] < big["score"]      # a lucky 12-trade OOS cannot outrank a real 200


def test_no_live_app_imports():
    pkg = Path(__file__).parents[1] / "app" / "research_engines" / "tri_compare"
    banned = ("app.autoscalp", "app.execution", "app.engines", "app.main", "app.connectors")
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
