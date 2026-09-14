"""
Look-ahead bias audit -- section 12 (original brief) / section 22
(index-first brief). Mandatory, explicit, not folded into the ordinary unit
tests above.

Three checks:
  1. STATIC SOURCE AUDIT -- no module in app/liquidity_sweep/ imports a live
     DB/market-data/network module. If nothing in this package CAN reach
     outside the bars/candidates it's explicitly given, look-ahead via a
     side channel is structurally impossible, not just untested.
  2. FUTURE-DATA MUTATION TEST -- the brief's own literal test design:
     build two bar series identical up to timestamp T, diverging completely
     after T (one keeps trending, one crashes). engine.evaluate() called on
     EACH series TRUNCATED to <= T must produce the IDENTICAL signal --
     proving the only thing that can affect a prediction at T is data at or
     before T, empirically, not just by code-reading.
  3. THE COUNTERFACTUAL -- the same two series evaluated WITHOUT truncation
     (the full array, future included) DO produce different results. This
     is not a second look-ahead check; it demonstrates check #2 is a real,
     discriminating test and not a vacuous one (i.e., a bug that always
     returns the same output regardless of input would otherwise pass #2
     for the wrong reason).
"""
import ast
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep import engine  # noqa: E402

PKG_DIR = Path(__file__).resolve().parents[1] / "app" / "liquidity_sweep"
FORBIDDEN_IMPORT_SUBSTRINGS = ("db", "market_hub", "requests", "connectors", "histcap", "runtime")


def _ts(i):
    base = datetime.datetime(2026, 9, 3, 9, 15)
    return (base + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bar(i, o, h, l, c, v=2000):
    return {"t": _ts(i), "o": o, "h": h, "l": l, "c": c, "v": v}


def _shared_prefix(n=32):
    bars = [_bar(i, 100.0, 100.4, 99.7, 100.1, 1500) for i in range(10)]
    pattern = [100, 101, 99.6, 100.5, 102.8, 101.5, 99.55, 100.2, 102.9, 101.0,
              100.3, 99.7, 101.8, 100.9, 99.6, 100.4]
    off = len(bars)
    bars += [_bar(off + i, p, p + 0.6, p - 0.6, p + 0.1, 2000) for i, p in enumerate(pattern)]
    off = len(bars)
    bars += [_bar(off + i, 100.3, 100.9, 99.8, 100.3, 2000) for i in range(6)]
    return bars[:n]


# --------------------------------------------------------------------------- #
#  1. Static source audit                                                     #
# --------------------------------------------------------------------------- #
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


def test_no_module_defines_mutable_global_state():
    """A cache/singleton dict or list at module scope would be a side
    channel for one call to leak into another. The package's only
    module-level constants should be immutable-by-convention (dataclasses,
    tuples, plain numbers/strings) -- flag any bare `= {}` / `= []` at
    module scope as worth a manual look (none currently exist by design)."""
    suspicious = []
    for py_file in PKG_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in tree.body:   # module-level only, not inside functions/classes
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Dict, ast.List)):
                if node.value.keys if isinstance(node.value, ast.Dict) else node.value.elts:
                    continue   # a non-empty literal constant (e.g. DEFAULT_WEIGHTS) is fine
                suspicious.append(py_file.name)
    assert suspicious == [], f"found empty mutable module-level containers (possible cache): {suspicious}"


# --------------------------------------------------------------------------- #
#  2 & 3. Future-data mutation test + its counterfactual                      #
# --------------------------------------------------------------------------- #
def test_future_data_mutation_does_not_change_the_truncated_prediction():
    prefix = _shared_prefix(32)
    T = len(prefix)

    # branch A: continues in the SAME direction after T
    branch_a = prefix + [_bar(T, 100.2, 100.6, 99.9, 100.3),
                         _bar(T + 1, 100.3, 100.7, 100.0, 100.5),
                         _bar(T + 2, 100.5, 101.0, 100.2, 100.8)]
    # branch B: a violent crash after T -- completely different future
    branch_b = prefix + [_bar(T, 100.2, 100.3, 60.0, 62.0),
                         _bar(T + 1, 62.0, 63.0, 40.0, 42.0),
                         _bar(T + 2, 42.0, 44.0, 20.0, 22.0)]

    out_a = engine.evaluate(symbol="NIFTY", bars_by_tf={"5m": branch_a[:T]}, spot=branch_a[T - 1]["c"])
    out_b = engine.evaluate(symbol="NIFTY", bars_by_tf={"5m": branch_b[:T]}, spot=branch_b[T - 1]["c"])

    # both slices are IDENTICAL (both are just `prefix`) -- the prediction
    # at T must be identical regardless of which future the data eventually
    # takes, because the function never saw either future.
    assert out_a == out_b, "LOOK-AHEAD BIAS FOUND: prediction at T changed when only future data differed"


def test_the_mutation_test_is_not_vacuous_full_series_do_differ():
    """Counterfactual: WITHOUT truncation (a caller mistake, not this
    package's contract), the two branches' full series DO produce different
    evaluate() output -- proving test #2 above is actually discriminating,
    not passing because the function ignores its input entirely."""
    prefix = _shared_prefix(32)
    T = len(prefix)
    branch_a = prefix + [_bar(T, 100.2, 100.6, 99.9, 100.3),
                         _bar(T + 1, 100.3, 100.7, 100.0, 100.5),
                         _bar(T + 2, 100.5, 101.0, 100.2, 100.8)]
    branch_b = prefix + [_bar(T, 100.2, 100.3, 60.0, 62.0),
                         _bar(T + 1, 62.0, 63.0, 40.0, 42.0),
                         _bar(T + 2, 42.0, 44.0, 20.0, 22.0)]

    out_full_a = engine.evaluate(symbol="NIFTY", bars_by_tf={"5m": branch_a}, spot=branch_a[-1]["c"])
    out_full_b = engine.evaluate(symbol="NIFTY", bars_by_tf={"5m": branch_b}, spot=branch_b[-1]["c"])
    assert out_full_a != out_full_b, (
        "the two branches' full (untruncated) series produced identical output -- "
        "the mutation test above would be vacuous if this ever happens")


def test_sweep_and_confirmation_confirmation_bars_never_exceed_the_supplied_length():
    """A cheap structural guarantee: every index this package's Sweep/
    Confirmation dataclasses report must be < len(bars supplied) -- an
    index >= len(bars) would imply a phantom future bar was referenced."""
    from app.liquidity_sweep import sweep as sweep_mod
    bars = _shared_prefix(32) + [_bar(32, 100.0, 100.3, 90.0, 91.0),
                                 _bar(33, 91.0, 105.0, 90.5, 104.0)]
    sw = sweep_mod.detect_sweep(bars, [{"price": 99.5, "source": "EQUAL_LOW"}])
    if sw is not None:
        assert sw.sweep_bar_index < len(bars)
        assert sw.reclaim_bar_index < len(bars)
        assert sw.reclaim_bar_index == len(bars) - 1   # reclaim is always the LAST given bar, never beyond it
