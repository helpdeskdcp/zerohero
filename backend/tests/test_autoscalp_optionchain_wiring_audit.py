"""
AUDIT LOCK — characterises the AutoScalp <-> option-chain wiring as it exists
today (2026-09-10 audit). Adds NO behaviour; it pins two claims that had no
direct test so a future change can't silently break them:

  1. The research/shadow `app/optionchain/` module (OptionStructureState /
     qualify / get_chain) is NOT on the AutoScalp decision path -- it must never
     be able to trigger a trade.
  2. `decide_from_context` degrades safely (NO_TRADE, no exception) on an
     empty / malformed / all-None option chain.

The positive wiring (chain OI -> S/R OI-walls -> state_score; CE/PE premium ->
ce_pe_confirmation / select_option / ev_gate; expiry-day runner overrides) is
already covered by test_sr_engine / test_state_classifier / test_autoscalp.
"""
import ast
import pathlib

import pytest

from app.engines.scalp_strategy import decide_from_context


# --------------------------------------------------------------------------- #
#  1. the new optionchain module is isolated from the decision path            #
# --------------------------------------------------------------------------- #
_DECISION_PATH = [
    "app/autoscalp/runner.py", "app/autoscalp/aggregator.py", "app/autoscalp/safeguards.py",
    "app/autoscalp/trade_features.py", "app/autoscalp/data_quality.py",
    "app/engines/scalp_strategy.py", "app/engines/sr_engine.py", "app/engines/option_engine.py",
    "app/engines/state_classifier.py", "app/engines/paper_trading.py", "app/engines/oi_math.py",
    "app/runtime.py", "app/backtest/runner.py", "app/backtest/replay.py",
    "app/backtest/oi_history_adapter.py", "app/backtest/calibration.py",
]


def test_optionchain_module_not_imported_by_autoscalp_decision_path():
    root = pathlib.Path(__file__).resolve().parents[1]
    hits = []
    for rel in _DECISION_PATH:
        f = root / rel
        if not f.exists():
            continue
        tree = ast.parse(f.read_text(), str(f))
        for node in ast.walk(tree):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module] if isinstance(node, ast.ImportFrom) and node.module
                    else [])
            for m in mods:
                if m == "app.optionchain" or (m or "").startswith("app.optionchain."):
                    hits.append(f"{rel}: imports {m}")
            # relative `from ..optionchain import ...` inside app/
            if (isinstance(node, ast.ImportFrom) and node.module
                    and "optionchain" in node.module.split(".") and node.level):
                hits.append(f"{rel}: relative import of optionchain")
    assert not hits, (
        "the research-only app/optionchain module leaked onto the AutoScalp "
        "decision path -- it must never be able to trigger a trade:\n" + "\n".join(hits))


def test_optionchain_structure_engine_declares_it_is_not_wired():
    """Its own contract still says so (guards against someone wiring it and
    forgetting to update the audit lock above)."""
    root = pathlib.Path(__file__).resolve().parents[1]
    txt = (root / "app/optionchain/__init__.py").read_text().lower()
    assert "no order path" in txt or "not wired" in txt or "research-only" in txt


# --------------------------------------------------------------------------- #
#  2. decide_from_context degrades safely on a bad / empty chain               #
# --------------------------------------------------------------------------- #
def _bars(px):
    out, prev = [], px[0]
    for i, c in enumerate(px):
        out.append([1_700_000_000 + i * 60, prev, max(prev, c) + 0.2, min(prev, c) - 0.2, c, 1000])
        prev = c
    return out


def _bars_by_tf(px):
    return {tf: _bars(px) for tf in ("1m", "3m", "5m", "15m")}


@pytest.mark.parametrize("chain", [
    None,
    [],
    [{"strike": None, "ce": None, "pe": None}],
    [{"strike": 100.0, "ce": {"ltp": None, "oi": None, "oi_chg": None},
      "pe": {"ltp": None, "oi": None, "oi_chg": None}}],
    [{"garbage": 1}],
])
def test_decide_from_context_safe_on_bad_chain(chain):
    px = [100 + (i % 7) * 0.3 for i in range(60)]          # a bland range path
    out = decide_from_context(_bars_by_tf(px), chain, atm=100.0, calib=None, config={})
    assert isinstance(out, dict)
    assert out.get("decision") in ("NO_TRADE", "BUY_CE", "BUY_PE", "WATCH")
    # a chain that carries no usable option data must not yield a live BUY plan
    if out.get("decision") in ("BUY_CE", "BUY_PE"):
        # would only be reachable with real per-leg premium -> our stubs have none
        pytest.fail(f"bad chain produced a tradeable decision: {out}")


def test_decide_from_context_no_exception_on_partial_chain():
    px = [100 + i * 0.1 for i in range(60)]
    chain = [{"strike": s, "ce": {"ltp": 5.0, "oi": 1000, "oi_chg": 0},
              "pe": {"ltp": 5.0}}                                # PE missing oi entirely
             for s in (98.0, 99.0, 100.0, 101.0, 102.0)]
    out = decide_from_context(_bars_by_tf(px), chain, atm=100.0, calib=None, config={})
    assert isinstance(out, dict) and "decision" in out
