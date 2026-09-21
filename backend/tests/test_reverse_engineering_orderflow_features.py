"""app.reverse_engineering.orderflow_features -- pure functions + honest stubs."""
from app.reverse_engineering import orderflow_features as of


def test_premium_momentum_needs_two_real_points():
    r = of.premium_momentum([("t1", None)])
    assert r["available"] is False


def test_premium_momentum_computes_real_change():
    r = of.premium_momentum([("t1", 80.0), ("t2", 160.0)])
    assert r["available"] is True
    assert r["momentum_pct"] == 100.0


def test_premium_acceleration_needs_three_points():
    r = of.premium_momentum([("t1", 100.0), ("t2", 110.0)])
    assert r["acceleration_pct"] is None
    r2 = of.premium_momentum([("t1", 100.0), ("t2", 110.0), ("t3", 132.0)])
    assert r2["acceleration_pct"] is not None


def test_oi_price_relationship_classifications():
    assert of.oi_price_relationship(
        [("t1", 100), ("t2", 150)], [("t1", 20), ("t2", 25)])["relationship"] == "LONG_BUILDUP"
    assert of.oi_price_relationship(
        [("t1", 100), ("t2", 150)], [("t1", 25), ("t2", 20)])["relationship"] == "SHORT_BUILDUP"
    assert of.oi_price_relationship(
        [("t1", 150), ("t2", 100)], [("t1", 25), ("t2", 20)])["relationship"] == "LONG_UNWINDING"
    assert of.oi_price_relationship(
        [("t1", 150), ("t2", 100)], [("t1", 20), ("t2", 25)])["relationship"] == "SHORT_COVERING"


def test_oi_price_relationship_data_unavailable():
    r = of.oi_price_relationship([("t1", 100)], [("t1", 20), ("t2", 25)])
    assert r["available"] is False


def test_price_volume_divergence():
    r = of.price_volume_divergence([("t1", 100), ("t2", 110)], [("t1", 1000), ("t2", 500)])
    assert r["available"] is True and r["divergence"] is True
    r2 = of.price_volume_divergence([("t1", 100), ("t2", 110)], [("t1", 500), ("t2", 1000)])
    assert r2["divergence"] is False


def test_honest_stubs_never_fabricate():
    for fn in (of.aggressive_buy_sell_pressure, of.volume_delta, of.cumulative_delta,
              of.absorption, of.sweep_aggression):
        r = fn("NIFTY")
        assert r == {"available": False, "reason": "NO_TICK_LEVEL_DATA"}


def test_depth_imbalance_delegates_to_orderflow_depth(monkeypatch):
    from app.orderflow import depth as d
    monkeypatch.setattr(d, "snapshot_for_symbol",
                        lambda sym, at_or_before=None: {"available": True, "orderflow_state": "BULLISH"})
    r = of.depth_imbalance("NIFTY")
    assert r == {"available": True, "orderflow_state": "BULLISH"}
