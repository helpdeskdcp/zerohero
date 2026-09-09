"""
TREND-SWING daily harness -- offline unit tests. No network.

Covers: daily resample sanity, engine causality + determinism, no-look-ahead
(entry at next open; truncating the future doesn't change a past bar's target),
purged walk-forward drops boundary-straddling trades, R/points consistency,
verdict is evidence-based (3-way), no live-app imports.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.research_engines.trend_swing import data as D           # noqa: E402
from app.research_engines.trend_swing import engine as E         # noqa: E402
from app.research_engines.trend_swing import harness as HN       # noqa: E402
from app.research_engines.trend_swing import metrics as MT       # noqa: E402
from app.research_engines.trend_swing import backtest as B       # noqa: E402
from app.research_engines.trend_swing.config import merged       # noqa: E402

CFG = merged()


def test_daily_resample_one_bar_per_session():
    bars, cap = D.load_daily("NIFTY", start="2015-01-01", end="2016-06-30")
    assert cap["n_days"] > 200
    ds = [b["date"] for b in bars]
    assert ds == sorted(ds) and len(ds) == len(set(ds))          # one per date, ordered
    for b in bars:
        assert b["l"] <= b["o"] <= b["h"] and b["l"] <= b["c"] <= b["h"]
        assert b["n"] >= 30                                       # thin days dropped


def test_engine_is_causal_and_deterministic():
    bars, _ = D.load_daily("NIFTY", start="2012-01-01", end="2020-12-31")
    f1 = E.build(bars, CFG)
    f2 = E.build(bars, CFG)
    assert [r["trend_state"] for r in f1] == [r["trend_state"] for r in f2]
    t1 = E.target_direction(f1, CFG)
    # truncating the future must not change any past bar's target
    ftr = E.build(bars[:1500], CFG)
    ttr = E.target_direction(ftr, CFG)
    assert ttr == t1[:1500]


def test_entry_is_next_open_no_lookahead():
    bars, _ = D.load_daily("NIFTY", start="2012-01-01", end="2022-12-31")
    frame = E.build(bars, CFG)
    tgt = E.target_direction(frame, CFG)
    sim = HN.simulate(bars, CFG, frame=frame, tgt=tgt)
    by_date = {b["date"]: i for i, b in enumerate(bars)}
    for t in sim["trades"]:
        ei = by_date[t["entry_date"]]
        # fill price is within [prev-day low, next-day high] neighbourhood of the
        # *next* bar's open -- never the signal bar's close
        nb = bars[ei + 1] if ei + 1 < len(bars) else bars[ei]
        assert abs(t["entry"] - nb["o"]) <= CFG["slippage_points"] + 1e-6
        assert t["exit_date"] >= t["entry_date"]
        assert abs(t["r_multiple"] - t["points"] / t["risk_points"]) < 5e-3


def test_purge_drops_boundary_straddling_and_embargo():
    dates = [f"2020-01-{d:02d}" for d in range(1, 29)]
    trades = [
        {"entry_date": "2020-01-05", "exit_date": "2020-01-08"},   # before boundary -> keep
        {"entry_date": "2020-01-09", "exit_date": "2020-01-14"},   # straddles 2020-01-12 -> drop
        {"entry_date": "2020-01-13", "exit_date": "2020-01-18"},   # opens in embargo -> drop
        {"entry_date": "2020-01-25", "exit_date": "2020-01-27"},   # after embargo -> keep
    ]
    kept = B._purge(trades, "2020-01-12", 3, dates)
    assert [t["entry_date"] for t in kept] == ["2020-01-05", "2020-01-25"]


def test_verdict_is_three_way_and_evidence_based():
    yr = {"2021": {"n": 6, "net_R": 1.0}, "2022": {"n": 6, "net_R": -1.0}}
    wf = [{"trades": {"n": 6, "expectancy_R": -0.3, "net_R": -1.0}} for _ in range(6)]
    neg = B._verdict(
        {"n": 60, "expectancy_R": -0.2}, {"n": 15, "expectancy_R": -0.3, "profit_factor": 0.4},
        {"sharpe": -0.8, "equity_maxdd_R": -8.0}, {"sharpe": -0.5}, yr, wf,
        0.7, {"crisis_alpha_positive": False}, CFG)
    assert neg["verdict"] == "NO-GO" and len(neg["why"]) >= 2

    wf_ok = [{"trades": {"n": 6, "expectancy_R": 0.2, "net_R": 1.0}} for _ in range(6)]
    yr_ok = {str(2016 + i): {"n": 6, "net_R": 1.0} for i in range(4)}
    goo = B._verdict(
        {"n": 60, "expectancy_R": 0.2}, {"n": 45, "expectancy_R": 0.25, "profit_factor": 1.6},
        {"sharpe": 1.1, "equity_maxdd_R": -5.0}, {"sharpe": 1.0}, yr_ok, wf_ok,
        0.5, {"crisis_alpha_positive": True}, CFG)
    assert goo["verdict"] == "GO"

    incon = B._verdict(
        {"n": 30, "expectancy_R": 0.05}, {"n": 8, "expectancy_R": 0.1, "profit_factor": 1.2},
        {"sharpe": 0.4, "equity_maxdd_R": -4.0}, {"sharpe": 0.5},
        {"2021": {"n": 6, "net_R": 1.0}}, wf_ok, 0.3, {"crisis_alpha_positive": True}, CFG)
    assert incon["verdict"] == "INCONCLUSIVE"


def test_no_live_app_imports():
    pkg = Path(__file__).parents[1] / "app" / "research_engines" / "trend_swing"
    banned = ("app.autoscalp", "app.execution", "app.engines", "app.main", "app.connectors", "app.hcs")
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
