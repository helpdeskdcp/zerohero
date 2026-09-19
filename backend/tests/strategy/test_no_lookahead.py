"""
Case 13: no look-ahead bias. Same mutation-test discipline used everywhere
else in this codebase (app.liquidity_sweep, app.index_signal_research):
two branches identical up to T, diverging completely after T, must produce
a BYTE-IDENTICAL verification when both are truncated to T -- plus a static
import audit and the non-vacuous counterfactual.
"""
import ast
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy.base_strategy import MarketFeatures
from app.strategy.config import StrategyConfig
from app.strategy.strategy_verifier import StrategyVerifier

PKG_DIR = Path(__file__).resolve().parents[2] / "app" / "strategy"
FORBIDDEN_IMPORT_SUBSTRINGS = ("db", "market_hub", "requests", "connectors", "histcap", "runtime")


def _bar(i, o, h, l, c, v=1000):
    t = (datetime.datetime(2026, 1, 5, 9, 15) + datetime.timedelta(minutes=5 * i)).isoformat()
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def _shared_prefix(n=40):
    return [_bar(i, 100 + i * 0.5, 100 + i * 0.5 + 0.4, 100 + i * 0.5 - 0.3, 100 + i * 0.5 + 0.2, 1000)
           for i in range(n)]


def test_no_module_in_the_package_imports_a_live_data_or_network_source():
    offenders = []
    for py_file in PKG_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                low = name.lower()
                if any(bad in low for bad in FORBIDDEN_IMPORT_SUBSTRINGS):
                    offenders.append((py_file.name, name))
    assert offenders == [], f"found imports of live/external data sources: {offenders}"


def test_verification_at_t_is_identical_regardless_of_future_bars():
    prefix = _shared_prefix(40)
    T = len(prefix)
    branch_a = prefix + [_bar(T, 100.2, 100.6, 99.9, 100.3), _bar(T + 1, 100.3, 100.7, 100.0, 100.5)]
    branch_b = prefix + [_bar(T, 100.2, 100.3, 60.0, 62.0), _bar(T + 1, 62.0, 63.0, 40.0, 42.0)]

    va = StrategyVerifier(StrategyConfig())
    vb = StrategyVerifier(StrategyConfig())
    out_a = va.verify(symbol="NIFTY", raw_signal="BUY",
                      features=MarketFeatures(bars=branch_a[:T]), timestamp=branch_a[T - 1]["t"])
    out_b = vb.verify(symbol="NIFTY", raw_signal="BUY",
                      features=MarketFeatures(bars=branch_b[:T]), timestamp=branch_b[T - 1]["t"])
    assert out_a.to_dict() == out_b.to_dict(), "LOOK-AHEAD BIAS: verification at T changed with only future data differing"


def test_the_mutation_test_is_not_vacuous_full_series_do_differ():
    prefix = _shared_prefix(40)
    T = len(prefix)
    branch_a = prefix + [_bar(T, 100.2, 100.6, 99.9, 100.3), _bar(T + 1, 100.3, 100.7, 100.0, 100.5)]
    branch_b = prefix + [_bar(T, 100.2, 100.3, 60.0, 62.0), _bar(T + 1, 62.0, 63.0, 40.0, 42.0)]

    va = StrategyVerifier(StrategyConfig())
    vb = StrategyVerifier(StrategyConfig())
    out_full_a = va.verify(symbol="NIFTY", raw_signal="BUY",
                           features=MarketFeatures(bars=branch_a), timestamp=branch_a[-1]["t"])
    out_full_b = vb.verify(symbol="NIFTY", raw_signal="BUY",
                           features=MarketFeatures(bars=branch_b), timestamp=branch_b[-1]["t"])
    assert out_full_a.to_dict() != out_full_b.to_dict(), (
        "the two branches' full series produced identical output -- "
        "the mutation test above would be vacuous if this ever happens")
