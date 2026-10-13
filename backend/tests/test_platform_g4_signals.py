"""G4: master-signal adapter, contract candidates, per-tenant capital-aware personalization."""
import pytest

from app.platform.signals import contracts as CT
from app.platform.signals import master as MS
from app.platform.signals import personalize as PZ
from app.platform.tenancy import TenantStore


def sig(**kw):
    base = dict(decision="BUY_CE", reason="SUPPORT_BOUNCE | regime TRENDING_UP", regime="TRENDING_UP", probability=0.61,
                confidence="MEDIUM", entry=100.0, stop_loss=80.0, calibration_status="fitted", index_ltp=24000.0)
    base.update(kw)
    return base


def leg(strike, delta, ltp, bid, ask, oi=5000, vol=2000, **kw):
    d = dict(strike=strike, ltp=ltp, bid=bid, ask=ask, delta=delta, oi=oi, volume=vol, iv=0.15, theta=-5.0)
    d.update(kw)
    return d


CHAIN = [leg(23900, .72, 190, 189, 191), leg(24000, .52, 110, 109.5, 110.5), leg(24100, .32, 55, 54.5, 55.5),
         leg(24200, .18, 25, 24.5, 25.5), leg(24050, .45, 80, 70, 90)]                # last one: 25% spread -> rejected


# ------------------------------------------------------------------ master
def test_adapter_maps_decisions_and_never_invents_confidence():
    ce = MS.from_autoscalp_sig(sig(), underlying="NIFTY", now=1.0)
    assert ce.direction == "CE" and ce.tradable and ce.confidence_pct == 61.0 and ce.confidence_calibrated
    assert ce.premium_sl_fraction == pytest.approx(0.2) and "SUPPORT_BOUNCE" in ce.reasons
    prior = MS.from_autoscalp_sig(sig(calibration_status="prior"), underlying="NIFTY")
    assert prior.confidence_pct is None and prior.confidence_calibrated is False and prior.engine_probability == 0.61
    assert MS.from_autoscalp_sig(sig(decision="BUY_PE"), underlying="NIFTY").direction == "PE"
    nt = MS.from_autoscalp_sig(sig(decision="NO_TRADE"), underlying="NIFTY")
    assert nt.direction == "NO_TRADE" and not nt.tradable


def test_unhealthy_data_forces_no_trade_with_the_reason():
    m = MS.from_autoscalp_sig(sig(), underlying="NIFTY", data_ok=False, data_reasons=("NO_HEALTHY_PROVIDER",))
    assert m.direction == "NO_TRADE" and not m.tradable and "NO_HEALTHY_PROVIDER" in m.blocked_reasons and m.confidence_pct is None


def test_bad_stop_loss_gives_no_sl_basis_instead_of_a_guess():
    assert MS.from_autoscalp_sig(sig(stop_loss=None), underlying="N").premium_sl_fraction is None
    assert MS.from_autoscalp_sig(sig(stop_loss=120.0), underlying="N").premium_sl_fraction is None


# ------------------------------------------------------------------ contracts
def test_bands_rank_with_reasons_and_never_estimate_missing_greeks():
    cands = CT.select_candidates(CHAIN + [leg(24300, None, 10, 9.5, 10.5)], spot=24000, option_type="CE", strike_step=100)
    elig = {b: [c.strike for c in v if c.status == "ELIGIBLE"] for b, v in cands.items()}
    assert elig["ITM"] == [23900] and elig["ATM"] == [24000] and set(elig["OTM"]) == {24100}
    rejected = {(b, c.strike): c.reject_reason for b, v in cands.items() for c in v if c.status == "REJECTED"}
    assert any(r for (b, s), r in rejected.items() if s == 24300)                    # missing delta -> rejected with a reason
    assert any("spread" in str(r).lower() for (b, s), r in rejected.items() if s == 24050)
    assert any("spread" in str(r).lower() for (b, s), r in rejected.items() if s == 24200)       # 4% spread > 3% hard filter


# ------------------------------------------------------------------ personalization
@pytest.fixture
def store(tmp_path):
    s = TenantStore(str(tmp_path / "p.db"))
    s.create_tenant("rich", "Rich", virtual_capital=2_000_000.0)
    s.create_tenant("poor", "Poor", virtual_capital=30_000.0)
    s.create_tenant("strict", "Strict", virtual_capital=2_000_000.0, risk={"max_open_positions": 1, "max_trade_loss_pct": 0.01})
    return s


def run(store, tid, state, master=None, **kw):
    m = master or MS.from_autoscalp_sig(sig(), underlying="NIFTY", now=1.0)
    cands = CT.select_candidates(CHAIN, spot=24000, option_type="CE", strike_step=100)
    return PZ.personalize(m, cands, store.scope(tid).profile(), state, lot_size=75, exchange="NSE", segment="NIFTY_OPTION", **kw)


def test_same_master_signal_gives_different_outcomes_per_tenant_capital(store):
    rich = run(store, "rich", PZ.TenantState(capital_available=2_000_000.0))
    poor = run(store, "poor", PZ.TenantState(capital_available=30_000.0))
    assert rich.eligible and rich.lots >= 1 and rich.entry_premium in (110.5, 191, 55.5)    # the ASK, never the LTP
    assert rich.premium_cost == pytest.approx(rich.entry_premium * rich.qty) and rich.max_loss > 0
    assert rich.cost_model_status == "UNCALIBRATED" and "COST_MODEL_UNCALIBRATED" in rich.warnings
    assert not poor.eligible and "NO_AFFORDABLE_CONTRACT_WITHIN_RISK_LIMITS" in poor.rejections      # 1% of 30k = Rs300 budget << one lot's risk
    assert {r["reason"] for r in poor.contract_rejections} & {"EXCEEDS_TRADE_LOSS_LIMIT", "INSUFFICIENT_CAPITAL"}
    assert rich.tenant_id == "rich" and poor.tenant_id == "poor"


def test_risk_profile_limits_each_tenant_independently(store):
    strict = run(store, "strict", PZ.TenantState(capital_available=2_000_000.0))
    assert not strict.eligible and any(r["reason"] == "EXCEEDS_TRADE_LOSS_LIMIT" for r in strict.contract_rejections)
    assert run(store, "rich", PZ.TenantState(open_positions=1, capital_available=2e6)).rejections == ["MAX_OPEN_POSITIONS"]
    assert "DAILY_LOSS_LIMIT_REACHED" in run(store, "rich", PZ.TenantState(realized_pnl_today=-50_000, capital_available=2e6)).rejections
    assert "CONSECUTIVE_LOSS_LIMIT_REACHED" in run(store, "rich", PZ.TenantState(consecutive_losses=4, capital_available=2e6)).rejections


def test_suspended_tenant_and_non_paper_modes_are_rejected(store):
    store.set_status("rich", "SUSPENDED", actor="admin")
    assert "TENANT_SUSPENDED" in run(store, "rich", PZ.TenantState(capital_available=2e6)).rejections
    prof = {**store.scope("poor").profile(), "execution_mode": "LIVE_AUTO"}                # even a hand-edited profile cannot go live
    m = MS.from_autoscalp_sig(sig(), underlying="NIFTY", now=1.0)
    cands = CT.select_candidates(CHAIN, spot=24000, option_type="CE", strike_step=100)
    ps = PZ.personalize(m, cands, prof, PZ.TenantState(capital_available=1e6), lot_size=75, exchange="NSE", segment="NIFTY_OPTION")
    assert not ps.eligible and "LIVE_NOT_AVAILABLE" in ps.rejections


def test_blocked_data_no_trade_and_missing_sl_basis_never_produce_an_order_plan(store):
    st = PZ.TenantState(capital_available=2e6)
    blocked = MS.from_autoscalp_sig(sig(), underlying="NIFTY", data_ok=False, data_reasons=("STALE_FEED",))
    ps = run(store, "rich", st, master=blocked)
    assert not ps.eligible and "DATA_NOT_OK" in ps.rejections and "STALE_FEED" in ps.rejections and ps.contract is None
    assert "MASTER_SIGNAL_NO_TRADE" in run(store, "rich", st, master=MS.from_autoscalp_sig(sig(decision="NO_TRADE"), underlying="N")).rejections
    assert "NO_STOP_LOSS_BASIS" in run(store, "rich", st, master=MS.from_autoscalp_sig(sig(stop_loss=None), underlying="N")).rejections
    ok = run(store, "rich", st, master=MS.from_autoscalp_sig(sig(stop_loss=None), underlying="N"), default_sl_fraction=0.25)
    assert ok.eligible                                                                  # an explicit operator default is allowed, a guess is not


def test_validated_cost_profile_adds_fees(store):
    s2 = store
    s2.create_tenant("mcx", "Mcx", virtual_capital=3_000_000.0)
    m = MS.from_autoscalp_sig(sig(), underlying="NATURALGAS", now=1.0)
    cands = CT.select_candidates([leg(300, .5, 10, 9.9, 10.1, oi=9000, vol=5000)], spot=300, option_type="CE", strike_step=5)
    ps = PZ.personalize(m, cands, s2.scope("mcx").profile(), PZ.TenantState(capital_available=3e6), lot_size=1250,
                        exchange="MCX", segment="NATURALGAS_OPTION")
    assert ps.cost_model_status == "OK" and ps.fees_round_trip and ps.fees_round_trip > 100 and ps.eligible


def test_ranker_default_stays_itm_only_for_existing_callers():
    from app.liquidity_sweep.strikes import rank_strikes
    out = rank_strikes([leg(24000, .65, 110, 109.5, 110.5), leg(23900, .65, 190, 189, 191)], spot=24000, option_type="CE",
                       delta_band=(0.60, 0.70))
    assert [c.status for c in out] == ["ELIGIBLE", "REJECTED"] and "not ITM" in out[1].reject_reason
