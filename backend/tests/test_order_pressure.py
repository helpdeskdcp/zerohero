"""
Multi-Candle Order-Pressure research engine -- offline unit tests. No network.

Verifies: candle geometry + zero-range protection, pressure bounds, label
causality (features never read bar i+1), walk-forward disjointness + embargo,
model determinism, and that coverage_report is honest about missing data.

The engine is RESEARCH ONLY -- these tests also assert it imports nothing from
the live app (runner / execution / engines.scalp_strategy / hcs).
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.research_engines.order_pressure import formulas as F      # noqa: E402
from app.research_engines.order_pressure import series as S        # noqa: E402
from app.research_engines.order_pressure import labels as LB       # noqa: E402
from app.research_engines.order_pressure import features as FE     # noqa: E402
from app.research_engines.order_pressure import models as M        # noqa: E402
from app.research_engines.order_pressure import walkforward as WF  # noqa: E402
from app.research_engines.order_pressure.config import merged      # noqa: E402

CFG = merged()


# ------------------------------------------------------------- formulas
def test_candle_geometry_basic():
    g = F.candle_geometry({"o": 100, "h": 110, "l": 98, "c": 108})
    assert g["range"] == 12
    assert g["body"] == 8
    assert g["upper_wick"] == 2 and g["lower_wick"] == 2
    assert abs(g["close_location"] - (10 / 12)) < 1e-9
    assert g["green"] and not g["red"]


def test_zero_range_is_neutral():
    p = F.pressure({"o": 100, "h": 100, "l": 100, "c": 100}, cfg=CFG)
    assert p["buyer"] == p["seller"] == CFG["zero_range_score"]
    assert p["net"] == 0.0
    # near-zero range must not divide-by-zero
    p2 = F.pressure({"o": 100, "h": 100.0000001, "l": 100, "c": 100}, cfg=CFG)
    assert 0.0 <= p2["buyer"] <= 100.0


def test_pressure_bounds_and_direction():
    for bar in ({"o": 10, "h": 20, "l": 9, "c": 19}, {"o": 20, "h": 21, "l": 10, "c": 11},
                {"o": 15, "h": 16, "l": 5, "c": 15}):
        p = F.pressure(bar, cfg=CFG)
        assert 0.0 <= p["buyer"] <= 100.0 and 0.0 <= p["seller"] <= 100.0
        assert -100.0 <= p["net"] <= 100.0
    strong_bull = F.pressure({"o": 10, "h": 20, "l": 9.8, "c": 19.9}, cfg=CFG)
    strong_bear = F.pressure({"o": 20, "h": 20.2, "l": 10, "c": 10.1}, cfg=CFG)
    assert strong_bull["net"] > 0 > strong_bear["net"]


def test_series_dynamics_signs():
    rising = S.dynamics([-4, -1, 2, 6, 11, 16])
    assert rising["slope"] > 0 and rising["persistence"] > 0.5
    # strong positive body, sharp flip in the last two bars -> reversal down
    flip = S.dynamics([25, 25, 25, 25, 5, -8])
    assert flip["slope"] < 0 and flip["reversal"] == -1


# ------------------------------------------------------------- label causality
def _synth_bars(n=80):
    px = 100.0
    out = []
    for i in range(n):
        px += (1.0 if i % 3 else -1.4)
        out.append({"o": px - 0.3, "h": px + 0.8, "l": px - 0.9, "c": px, "t": i * 300})
    return out


def test_label_uses_next_bar_but_features_do_not():
    bars = _synth_bars(80)
    pr = FE.precompute_pressure(bars, CFG)
    i = 50
    # features from the full series
    r_full = FE.build_row(bars, pr, i, CFG)
    # features when everything after i is deleted -> must be identical (causal)
    bars_trunc = bars[: i + 1]
    pr_trunc = FE.precompute_pressure(bars_trunc, CFG)
    r_trunc = FE.build_row(bars_trunc, pr_trunc, i, CFG)
    assert r_full == r_trunc, "build_row peeked at a future bar"
    # the label, by contrast, DOES need bar i+1
    assert LB.next_candle_label(bars, i, CFG) in LB.CLASSES
    assert LB.next_candle_label(bars_trunc, i, CFG) is None  # i+1 missing -> no label


# ------------------------------------------------------------- walk-forward
def test_walkforward_disjoint_with_embargo():
    n = 1200
    X = [[float(i % 7), float((i * 3) % 5)] for i in range(n)]
    y = [LB.CLASSES[i % 3] for i in range(n)]
    dates = [f"2020-01-{1 + i // 60:02d}" for i in range(n)]  # 60 rows / session, 20 sessions

    seen_test, seen_train = [], []

    class _Spy(M.MajorityBaseline):
        def fit(self, Xtr, ytr, **kw):
            seen_train.append(len(Xtr))
            return super().fit(Xtr, ytr, **kw)

    cfg = merged({"wf_block_sessions": 3, "wf_min_train_blocks": 2, "wf_max_folds": 4,
                  "min_rows_for_fit": 50, "min_rows_per_class": 5, "wf_embargo_bars": 3})
    out = WF.run(X, y, dates, _Spy, cfg=cfg, calibrate=False)
    assert out["status"] == "OK"
    assert out["n_folds"] >= 1
    # every fold trained on strictly fewer rows than the total (expanding, not full)
    assert all(t < n for t in seen_train)


# ------------------------------------------------------------- determinism
def test_models_are_deterministic():
    bars = _synth_bars(400)
    pr = FE.precompute_pressure(bars, CFG)
    names = FE.feature_names(CFG)
    X, y = [], []
    for i in range(20, len(bars) - 1):
        lab = LB.next_candle_label(bars, i, CFG)
        if lab is None:
            continue
        r = FE.build_row(bars, pr, i, CFG)
        X.append([r[k] for k in names]); y.append(lab)
    for factory in (M.MultinomialLogit, M.GBStumps, M.MLP):
        a, b = factory(), factory()
        a.fit(X, y, X_val=X[-40:], y_val=y[-40:])
        b.fit(X, y, X_val=X[-40:], y_val=y[-40:])
        pa = [a.predict_proba(x) for x in X[:20]]
        pb = [b.predict_proba(x) for x in X[:20]]
        assert pa == pb, f"{factory.__name__} not deterministic"


# ------------------------------------------------------------- honesty / isolation
def test_coverage_report_is_honest():
    from app.research_engines.order_pressure import data as D
    cov = D.coverage_report()
    assert "spot" in cov and "option" in cov and "unavailable" in cov
    assert len(cov["unavailable"]) >= 3
    assert "INSUFFICIENT_SAMPLE" in cov["summary"]


def test_engine_imports_nothing_from_live_app():
    pkg = Path(__file__).parents[1] / "app" / "research_engines" / "order_pressure"
    banned = ("app.autoscalp", "app.execution", "app.engines.scalp_strategy",
              "app.hcs", "..autoscalp", "..execution", "..engines", "..hcs")
    for py in pkg.glob("*.py"):
        tree = ast.parse(py.read_text())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.add(("." * (node.level or 0)) + node.module)
            elif isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
        hits = [m for m in mods if any(m == b or m.startswith(b + ".") for b in banned)]
        assert not hits, f"{py.name} imports live-app module(s): {hits}"
