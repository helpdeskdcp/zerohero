"""
New analytics added from a feature comparison against
Quantech-innovation/options-flow-ml-workstation (an external ML-based
options-flow project, synthetic-data-only): OI Build-Up Regime
classification and Weighted Chain IV. Net GEX / Max Pain / PCR / IV Skew
already existed in app.optionchain.analytics before this comparison and
are NOT duplicated here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.optionchain.analytics import (  # noqa: E402
    FLAT_OI, LONG_BUILDUP, LONG_UNWINDING, SHORT_BUILDUP, SHORT_COVERING,
    oi_buildup_regime, weighted_chain_iv,
)
from app.optionchain.chain import OptionChain, OptionLeg, StrikeRow  # noqa: E402


def _chain(spot, oi_by_strike, iv=0.12, ts="2026-09-09T10:00:00Z"):
    rows = [StrikeRow(strike=float(k), ce=OptionLeg(ltp=100.0, oi=ce_oi, iv=iv),
                      pe=OptionLeg(ltp=100.0, oi=pe_oi, iv=iv))
           for k, (ce_oi, pe_oi) in oi_by_strike.items()]
    return OptionChain(underlying="NIFTY", expiry="15SEP2026", ts=ts, source="unit",
                       spot=spot, rows=rows).sort().compute_atm()


def test_price_up_and_oi_up_is_long_buildup():
    baseline = _chain(23400, {23500: (1000.0, 1000.0)})
    now = _chain(23500, {23500: (1500.0, 1000.0)})       # CE OI rose, price rose
    r = oi_buildup_regime(now, baseline)
    assert r.status == "ok" and r.price_direction == "UP"
    assert r.ce_regime == LONG_BUILDUP


def test_price_down_and_oi_up_is_short_buildup():
    baseline = _chain(23500, {23500: (1000.0, 1000.0)})
    now = _chain(23400, {23500: (1500.0, 1000.0)})       # CE OI rose, price FELL
    r = oi_buildup_regime(now, baseline)
    assert r.price_direction == "DOWN"
    assert r.ce_regime == SHORT_BUILDUP


def test_price_up_and_oi_down_is_short_covering():
    baseline = _chain(23400, {23500: (1000.0, 1000.0)})
    now = _chain(23500, {23500: (600.0, 1000.0)})        # CE OI fell, price rose
    r = oi_buildup_regime(now, baseline)
    assert r.ce_regime == SHORT_COVERING


def test_price_down_and_oi_down_is_long_unwinding():
    baseline = _chain(23500, {23500: (1000.0, 1000.0)})
    now = _chain(23400, {23500: (600.0, 1000.0)})        # CE OI fell, price fell
    r = oi_buildup_regime(now, baseline)
    assert r.ce_regime == LONG_UNWINDING


def test_ce_and_pe_regimes_are_classified_independently():
    baseline = _chain(23400, {23500: (1000.0, 1000.0)})
    now = _chain(23500, {23500: (1500.0, 600.0)})   # CE OI up (long build-up), PE OI down (short covering)
    r = oi_buildup_regime(now, baseline)
    assert r.ce_regime == LONG_BUILDUP
    assert r.pe_regime == SHORT_COVERING


def test_flat_price_is_flat_regime_not_a_crash():
    baseline = _chain(23500, {23500: (1000.0, 1000.0)})
    now = _chain(23500, {23500: (1500.0, 1500.0)})
    r = oi_buildup_regime(now, baseline)
    assert r.price_direction == "FLAT"
    assert r.ce_regime == FLAT_OI and r.pe_regime == FLAT_OI


def test_missing_spot_degrades_without_crashing():
    baseline = _chain(23500, {23500: (1000.0, 1000.0)})
    now = _chain(None, {23500: (1500.0, 1000.0)})
    r = oi_buildup_regime(now, baseline)
    assert r.status == "no_spot"


def test_weighted_chain_iv_is_oi_weighted_average():
    c = _chain(23500, {23400: (1000.0, 0.0), 23600: (3000.0, 0.0)}, iv=0.10)
    c.rows[0].ce.iv = 0.10
    c.rows[1].ce.iv = 0.20
    r = weighted_chain_iv(c)
    assert r.status == "ok"
    # (1000*0.10 + 3000*0.20) / 4000 = 0.175
    assert abs(r.value - 0.175) < 1e-6


def test_weighted_chain_iv_empty_chain_does_not_crash():
    empty = OptionChain(underlying="NIFTY", expiry="15SEP2026", ts="t", source="x", rows=[])
    assert weighted_chain_iv(empty).status == "empty"


def test_weighted_chain_iv_no_oi_or_iv_does_not_crash():
    c = OptionChain(underlying="NIFTY", expiry="15SEP2026", ts="t", source="x",
                    rows=[StrikeRow(strike=23500.0, ce=OptionLeg(ltp=100.0))])
    assert weighted_chain_iv(c).status == "no_oi_or_iv"
