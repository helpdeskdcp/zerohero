from app.hedging.primary_selector import select_primary
from app.optionchain.chain import OptionChain, OptionLeg, StrikeRow


def _chain(spot, rows):
    return OptionChain(underlying="NIFTY", expiry="29SEP2026", ts="t", source="test",
                       spot=spot, rows=rows)


def test_selects_the_largest_qualifying_oi_wall():
    rows = [
        StrikeRow(strike=23200, ce=OptionLeg(ltp=50, oi=1000), pe=OptionLeg(ltp=40, oi=800)),
        StrikeRow(strike=23400, ce=OptionLeg(ltp=30, oi=5000), pe=OptionLeg(ltp=20, oi=1200)),
        StrikeRow(strike=22800, ce=OptionLeg(ltp=10, oi=100), pe=OptionLeg(ltp=60, oi=2000)),
    ]
    out = select_primary(_chain(23000, rows), min_distance_pct=0.5, min_oi=500)
    assert out.status == "SELECTED"
    assert out.side == "CE"
    assert out.leg.strike == 23400   # CE wall (oi=5000) beats PE wall (max 2000)


def test_ignores_strikes_below_min_distance():
    rows = [StrikeRow(strike=23010, ce=OptionLeg(ltp=50, oi=99999), pe=None)]  # too close to spot
    out = select_primary(_chain(23000, rows), min_distance_pct=5.0, min_oi=500)
    assert out.status == "NO_TRADE"


def test_ignores_strikes_below_min_oi():
    rows = [StrikeRow(strike=23400, ce=OptionLeg(ltp=50, oi=10), pe=None)]
    out = select_primary(_chain(23000, rows), min_distance_pct=0.5, min_oi=500)
    assert out.status == "NO_TRADE"


def test_forced_side_ignores_the_other_side():
    rows = [
        StrikeRow(strike=23400, ce=OptionLeg(ltp=30, oi=100), pe=None),
        StrikeRow(strike=22600, ce=None, pe=OptionLeg(ltp=20, oi=99999)),
    ]
    out = select_primary(_chain(23000, rows), side="CE", min_distance_pct=0.5, min_oi=50)
    assert out.status == "SELECTED" and out.side == "CE"


def test_no_spot_is_no_trade_not_a_crash():
    out = select_primary(_chain(None, [StrikeRow(strike=100, ce=OptionLeg(ltp=1, oi=1000))]))
    assert out.status == "NO_TRADE"


def test_itm_strikes_never_qualify_as_primary():
    # CE strike below spot (ITM) must never be selected as the sold leg
    rows = [StrikeRow(strike=22900, ce=OptionLeg(ltp=150, oi=99999), pe=None)]
    out = select_primary(_chain(23000, rows), min_distance_pct=0.5, min_oi=500)
    assert out.status == "NO_TRADE"
