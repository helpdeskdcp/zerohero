"""
HCS Tier-A multinomial-logit shadow model -- offline unit tests. No network.

Verifies:
  * hcs/labels: 3-class + quality + win labels, boundary + override cases
  * MultinomialLogit: softmax is a proper distribution; deterministic training
  * Isotonic (PAV): output is monotone non-decreasing
  * score(): INSUFFICIENT below the row floor; OK shape when a model exists
  * refit_and_report(): shape; never writes unless persist=True
  * SHADOW isolation: the module imports nothing from the runner / execution /
    scalp_strategy and never references live_trading
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.hcs import adaptive_mc as MC   # noqa: E402
from app.hcs import labels as LAB       # noqa: E402


# --------------------------------------------------------------------------- labels
def _row(**kw):
    base = dict(entry=100.0, stop_loss=80.0, points=0.0, r_multiple=None,
                outcome="WIN", exit_reason="TIME")
    base.update(kw)
    return base


def test_three_class_thresholds():
    # risk = 20 ; THETA_R = 0.5 -> +/-10 points is the boundary
    assert LAB.three_class(_row(points=12.0, outcome="WIN", exit_reason="TIME")) == "UP"
    assert LAB.three_class(_row(points=-12.0, outcome="LOSS", exit_reason="TIME")) == "DOWN"
    assert LAB.three_class(_row(points=3.0, outcome="WIN", exit_reason="TIME")) == "NO_MOVE"
    assert LAB.three_class(_row(points=-3.0, outcome="LOSS", exit_reason="TIME")) == "NO_MOVE"


def test_three_class_exit_reason_overrides():
    # TARGET / STOP win regardless of the tiny recorded points
    assert LAB.three_class(_row(points=0.1, outcome="WIN", exit_reason="TARGET")) == "UP"
    assert LAB.three_class(_row(points=-0.1, outcome="LOSS", exit_reason="STOP")) == "DOWN"


def test_flat_is_no_move_and_unresolved_is_none():
    assert LAB.three_class(_row(outcome="FLAT", exit_reason="TIME_NODATA")) == "NO_MOVE"
    assert LAB.three_class(_row(outcome="PENDING")) is None


def test_quality_and_win_labels():
    assert LAB.y_win(_row(outcome="WIN")) == 1
    assert LAB.y_win(_row(outcome="LOSS")) == 0
    assert LAB.y_win(_row(outcome="FLAT")) is None
    # winsor: r = points/risk = 200/20 = 10 -> clamped to _R_HI
    assert LAB.quality_target(_row(points=200.0)) == LAB._R_HI
    assert LAB.quality_target(_row(entry=None, points=None, r_multiple=None)) is None


# --------------------------------------------------------------------------- model
def _feat(i):
    # a small deterministic feature vector
    return {"signal_score": 0.1 * (i % 5), "b": 0.2, "reg_RANGE": float(i % 2)}


def test_softmax_is_a_distribution():
    m = MC.MultinomialLogit()
    p = m.predict(_feat(3))
    assert set(p) == set(LAB.CLASSES)
    assert abs(sum(p.values()) - 1.0) < 1e-9
    assert all(0.0 <= v <= 1.0 for v in p.values())


def test_multinomial_training_is_deterministic():
    data = [(_feat(i), LAB.CLASSES[i % 3]) for i in range(30)]
    def fit():
        m = MC.MultinomialLogit()
        for _ in range(10):
            for x, y in data:
                m.update(x, y, 0.05)
        return m.to_dict()
    assert fit() == fit()


def test_isotonic_is_monotone():
    pairs = [(0.1, 0), (0.2, 1), (0.3, 0), (0.4, 1), (0.5, 1), (0.6, 0), (0.9, 1)]
    iso = MC.Isotonic().fit(pairs)
    grid = [i / 20 for i in range(21)]
    ys = [iso.predict(p) for p in grid]
    assert all(b >= a - 1e-9 for a, b in zip(ys, ys[1:])), ys
    assert all(0.0 <= y <= 1.0 for y in ys)


def test_ridge_moves_toward_target():
    r = MC.Ridge(mean=0.0)
    x = {"a": 1.0}
    for _ in range(200):
        r.update(x, 2.0, 0.05)
    assert 1.0 < r.predict(x) < 3.0


# --------------------------------------------------------------------------- served
def test_score_insufficient_when_no_data(monkeypatch):
    monkeypatch.setattr(MC, "_resolved_rows", lambda: [])
    MC._CACHE.update(bundle=None, rows=-1)
    out = MC.score({"signal_score": 60})
    assert out["status"] == "INSUFFICIENT"
    assert out["min_rows"] == MC._MIN_ROWS


def _synth_rows(n=60):
    rows = []
    for i in range(n):
        win = i % 2 == 0
        rows.append(dict(
            signal_score=55 + (i % 20), momentum=None, mtf_alignment=(i % 5) - 2,
            regime="TRENDING_DOWN" if i % 3 else "RANGE",
            signal_type="SUPPORT_BREAKDOWN" if i % 2 else "SUPPORT_REVERSAL",
            tod_bucket="MORNING", rr=1.5, ev=8.0, ev_r=None, confidence="MEDIUM",
            probability=0.55, entry=100.0, stop_loss=80.0,
            points=15.0 if win else -15.0, r_multiple=None,
            mfe=20.0, mae=5.0, exit_reason="TARGET" if win else "STOP",
            outcome="WIN" if win else "LOSS",
            created_ts=f"2026-09-0{1 + i % 6}T0{i % 6}:00:00+00:00",
            session_date=f"2026-09-0{1 + i % 6}"))
    return rows


def test_score_ok_shape(monkeypatch):
    monkeypatch.setattr(MC, "_resolved_rows", lambda: _synth_rows(60))
    MC._CACHE.update(bundle=None, rows=-1)
    out = MC.score({"signal_score": 70, "regime": "TRENDING_DOWN",
                    "signal_type": "SUPPORT_BREAKDOWN", "entry": 100, "stop_loss": 80})
    assert out["status"] == "OK"
    for k in ("p_up", "p_down", "p_no_move", "p_win_raw", "p_win_cal",
              "exp_r", "setup_rank_score"):
        assert k in out
    assert abs(out["p_up"] + out["p_down"] + out["p_no_move"] - 1.0) < 1e-3
    assert 0.0 <= out["p_win_cal"] <= 1.0
    assert out["setup_rank_score"] >= 0.0


def test_refit_and_report_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(MC, "_resolved_rows", lambda: _synth_rows(72))
    monkeypatch.setattr(MC, "_STATE", str(tmp_path / "mc.json"))
    rep = MC.refit_and_report(persist=False)
    assert rep["available"] is True
    assert rep["version"] == MC.MODEL_VERSION
    agg = rep["walk_forward"]["aggregate"]
    assert "mc_logloss" in agg and "p_win_cal" in agg and "baseline_logistic" in agg
    assert rep["persisted"] is False
    assert not (tmp_path / "mc.json").exists()          # persist=False writes nothing


def test_refit_reports_unavailable_below_floor(monkeypatch):
    monkeypatch.setattr(MC, "_resolved_rows", lambda: _synth_rows(10))
    rep = MC.refit_and_report()
    assert rep["available"] is False and rep["n"] == 10


# --------------------------------------------------------------------------- shadow isolation
def test_module_has_no_live_trading_coupling():
    src = Path(__file__).parents[1] / "app" / "hcs" / "adaptive_mc.py"
    tree = ast.parse(src.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    banned = {"app.autoscalp.runner", "app.execution", "app.engines.scalp_strategy",
              "..autoscalp.runner", "..execution", "..engines.scalp_strategy"}
    assert not (imported & banned), imported & banned
    # `live_trading` may appear in the docstring but must not be referenced in code
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "live_trading" not in names | attrs
