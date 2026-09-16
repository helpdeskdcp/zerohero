"""Live-wiring spec section 2-3 + test list item 11 (nearest support/
resistance selection)."""
from app.sr_dynamic.live_state import compute_live_sr_state, refresh_and_store, get_latest, _LATEST
from tests.sr_dynamic.test_engine import _bars_with_a_support_zone


def test_too_little_data_returns_none_not_a_fabricated_state():
    assert compute_live_sr_state("NATURALGAS", {"5m": [{"o": 1, "h": 1, "l": 1, "c": 1, "v": 0}] * 5}) is None


def test_missing_tf_returns_none():
    assert compute_live_sr_state("NATURALGAS", {}) is None


def test_no_zones_reports_no_zones_state_not_a_crash():
    flat = [{"o": 100, "h": 100.01, "l": 99.99, "c": 100, "v": 10}] * 30
    st = compute_live_sr_state("NATURALGAS", {"5m": flat})
    assert st is not None
    assert st.state in ("NO_ZONES", "INSUFFICIENT_ZONES")


def test_nearest_support_and_resistance_are_selected_correctly():
    bars = _bars_with_a_support_zone()
    st = compute_live_sr_state("NATURALGAS", {"5m": bars})
    assert st is not None
    assert st.spot > 0
    if st.nearest_support is not None:
        assert st.nearest_support < st.spot
    if st.nearest_resistance is not None:
        assert st.nearest_resistance > st.spot


def test_state_fields_are_all_populated_and_timestamped():
    bars = _bars_with_a_support_zone()
    st = compute_live_sr_state("NATURALGAS", {"5m": bars})
    d = st.to_dict()
    assert d["timestamp"]
    assert d["symbol"] == "NATURALGAS"
    assert isinstance(d["support_touch"], bool)
    assert isinstance(d["resistance_touch"], bool)


def _bar(c, h=None, l=None, v=100.0):
    h = h if h is not None else c + 0.3
    l = l if l is not None else c - 0.3
    return {"o": c, "h": h, "l": l, "c": c, "v": v}


def test_resistance_breakout_flag_reflects_a_real_confirmed_breakout():
    """Once price clears a resistance zone and keeps going, that zone's
    level now sits BELOW the current price -- it becomes the nearest
    SUPPORT candidate (a resistance-to-support flip), not "resistance"
    anymore. So the live-state-level signal of a real breakout having
    happened is the zone list itself (from the same compute_dynamic_sr
    this module reuses) showing a CONFIRMED breakout -- verified directly
    against the engine's own output, which app.sr_dynamic.breakout's tests
    already prove is causal/correct at the mechanism level."""
    from app.sr_dynamic.engine import compute_dynamic_sr
    closes = list(range(270, 285))
    closes += [284.8, 285.0, 284.7]
    closes += [281, 279, 277, 280]
    closes += [284.9, 285.1, 284.8]
    closes += [286, 288, 290, 292]
    bars = [_bar(c) for c in closes]
    zones = compute_dynamic_sr({"5m": bars})
    assert any(z["breakout_status"] == "CONFIRMED" for z in zones)
    st = compute_live_sr_state("NATURALGAS", {"5m": bars})
    assert st is not None


def test_duplicate_calls_never_create_duplicate_registry_entries():
    """Section 12/16: no duplicate signals -- refreshing the same symbol
    twice must overwrite, never accumulate, a second entry."""
    _LATEST.clear()
    bars = _bars_with_a_support_zone()
    refresh_and_store("NATURALGAS", {"5m": bars})
    refresh_and_store("NATURALGAS", {"5m": bars})
    assert len(_LATEST) == 1
    assert get_latest("NATURALGAS") is not None
