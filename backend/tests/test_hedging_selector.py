"""app.hedging.selector -- full-chain hedge evaluation/selection. Uses the
real app.optionchain.chain.OptionChain/StrikeRow/OptionLeg dataclasses
(the actual data model this module is built to consume), never a mocked
substitute schema."""
import pytest

from app.optionchain.chain import OptionChain, StrikeRow, OptionLeg
from app.hedging import selector as S


def _leg(ltp, *, oi=1000, volume=500, bid=None, ask=None, delta=None):
    bid = bid if bid is not None else round(ltp * 0.98, 2)
    ask = ask if ask is not None else round(ltp * 1.02, 2)
    return OptionLeg(ltp=ltp, bid=bid, ask=ask, oi=oi, volume=volume, delta=delta)


def _chain(rows, spot=24500):
    return OptionChain(underlying="NIFTY", expiry="25SEP2026", ts="2026-09-22T00:00:00Z",
                       source="test", spot=spot, rows=rows)


def _ce_chain():
    return _chain([
        StrikeRow(strike=24500, ce=_leg(60.0, delta=0.5)),                       # primary strike itself
        StrikeRow(strike=24600, ce=_leg(35.0, delta=0.35)),
        StrikeRow(strike=24700, ce=_leg(20.0, delta=0.20)),                      # cheapest valid hedge
        StrikeRow(strike=24800, ce=_leg(10.0, oi=50, volume=10, delta=0.10)),     # illiquid -> rejected
        StrikeRow(strike=24400, ce=_leg(90.0, delta=0.65)),                      # not further OTM -> excluded
    ])


def _primary_ce():
    return S.PrimaryLeg(strike=24500, option_type="CE", premium=60.0, delta=0.5, lot_size=65)


def test_evaluate_candidates_only_considers_further_otm_strikes():
    cands = S.evaluate_candidates(_ce_chain(), _primary_ce())
    strikes = {c.strike for c in cands}
    assert strikes == {24600, 24700, 24800}     # 24400 and 24500 excluded entirely


def test_evaluate_candidates_rejects_illiquid_strike():
    cands = S.evaluate_candidates(_ce_chain(), _primary_ce())
    c800 = next(c for c in cands if c.strike == 24800)
    assert not c800.accepted
    assert any("oi" in r or "volume" in r for r in c800.reject_reasons)


def test_evaluate_candidates_computes_real_economics_for_accepted_ones():
    cands = S.evaluate_candidates(_ce_chain(), _primary_ce())
    c700 = next(c for c in cands if c.strike == 24700)
    assert c700.accepted
    hedge_ask = 20.0 * 1.02   # _leg()'s default ask markup -- the realistic BUY fill, not ltp
    assert c700.premium == pytest.approx(hedge_ask)
    assert c700.economics.max_loss_per_lot == pytest.approx(200 * 65 - (60.0 - hedge_ask) * 65)


def test_select_hedge_picks_cheapest_accepted_candidate():
    dec = S.select_hedge(_ce_chain(), _primary_ce())
    assert dec.status == "SELECTED"
    assert dec.candidate.strike == 24700    # cheapest of {24600, 24700} that's accepted
    assert dec.accepted == 2                # 24600 and 24700 accepted, 24800 rejected


def test_select_hedge_no_trade_when_nothing_clears_the_bar():
    chain = _chain([StrikeRow(strike=24700, ce=_leg(20.0, oi=10, volume=1))])  # illiquid only
    dec = S.select_hedge(chain, _primary_ce())
    assert dec.status == "NO_TRADE"
    assert dec.candidate is None


def test_select_hedge_no_trade_when_chain_has_no_valid_strikes_at_all():
    chain = _chain([])
    dec = S.select_hedge(chain, _primary_ce())
    assert dec.status == "NO_TRADE" and dec.evaluated == 0


def test_select_hedge_rejects_hedge_that_eats_too_much_credit():
    # hedge premium 50 vs primary credit 60 -> 83% of credit, over the 60% default cap
    chain = _chain([StrikeRow(strike=24700, ce=_leg(50.0))])
    dec = S.select_hedge(chain, _primary_ce())
    assert dec.status == "NO_TRADE"


def test_select_hedge_sizes_the_position_from_capital():
    # best candidate's max_loss_per_lot is 10426 (see the economics test above,
    # using the realistic ask fill 20.4, not ltp 20.0);
    # 2,000,000 * 2% = 40,000 risk budget -> floor(40000/10426) = 3 lots.
    dec = S.select_hedge(_ce_chain(), _primary_ce(), available_capital=2_000_000, max_risk_pct=0.02)
    assert dec.status == "SELECTED"
    assert dec.lots == 3


def test_select_hedge_exposes_all_evaluated_candidates_for_audit():
    dec = S.select_hedge(_ce_chain(), _primary_ce())
    assert dec.status == "SELECTED"
    assert {c.strike for c in dec.all_candidates} == {24600, 24700, 24800}
    rejected = [c for c in dec.all_candidates if not c.accepted]
    assert len(rejected) == 1 and rejected[0].strike == 24800


def test_select_hedge_no_trade_when_capital_too_small_for_one_lot():
    dec = S.select_hedge(_ce_chain(), _primary_ce(), available_capital=100, max_risk_pct=0.02)
    assert dec.status == "NO_TRADE"
    assert "sizing rejected" in dec.reason


def test_hedge_leg_fills_at_ask_not_ltp():
    # The hedge leg is BOUGHT -- MCP 3.1.2 gap: paper execution must not
    # silently fill at LTP.
    chain = _chain([StrikeRow(strike=24700, ce=_leg(20.0, bid=19.5, ask=20.6))])
    cands = S.evaluate_candidates(chain, _primary_ce())
    c = cands[0]
    assert c.premium == 20.6 and c.premium != 20.0


def test_hedge_leg_rejected_when_ask_missing():
    chain = _chain([StrikeRow(strike=24700, ce=OptionLeg(ltp=20.0, bid=19.5, ask=None,
                                                          oi=1000, volume=500))])
    cands = S.evaluate_candidates(chain, _primary_ce())
    c = cands[0]
    assert not c.accepted
    assert any("bid/ask UNAVAILABLE" in r for r in c.reject_reasons)


def test_pe_side_only_considers_lower_strikes():
    chain = _chain([
        StrikeRow(strike=24500, pe=_leg(55.0, delta=-0.5)),
        StrikeRow(strike=24400, pe=_leg(35.0, delta=-0.35)),
        StrikeRow(strike=24300, pe=_leg(18.0, delta=-0.18)),
        StrikeRow(strike=24600, pe=_leg(90.0, delta=-0.65)),   # wrong direction, excluded
    ])
    primary = S.PrimaryLeg(strike=24500, option_type="PE", premium=55.0, delta=-0.5, lot_size=65)
    cands = S.evaluate_candidates(chain, primary)
    assert {c.strike for c in cands} == {24400, 24300}
