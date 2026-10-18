import math

import pytest

from app.strategy_lab import straddle_scan as SS


def bars_for(entry_ce=100.0, entry_pe=100.0, exit_ce=60.0, exit_pe=40.0, day="2026-10-13", prev="2026-10-12", k=23000):
    b = {(k, "CE"): {}, (k, "PE"): {}, (k + 50, "CE"): {}, (k - 50, "PE"): {}}
    for t in ("09:20", "10:30", "12:00", "13:30"):
        b[(k, "CE")][f"{day} {t}"] = (entry_ce, entry_ce, entry_ce, entry_ce)
        b[(k, "PE")][f"{day} {t}"] = (entry_pe, entry_pe, entry_pe, entry_pe)
    b[(k, "CE")][f"{day} 15:10"] = (exit_ce, exit_ce, exit_ce, exit_ce)
    b[(k, "PE")][f"{day} 15:10"] = (exit_pe, exit_pe, exit_pe, exit_pe)
    b[(k + 50, "CE")][f"{day} 09:20"] = (50.0, 50.0, 50.0, 50.0); b[(k + 50, "CE")][f"{day} 15:10"] = (20.0, 20.0, 20.0, 20.0)
    b[(k - 50, "PE")][f"{day} 09:20"] = (45.0, 45.0, 45.0, 45.0); b[(k - 50, "PE")][f"{day} 15:10"] = (15.0, 15.0, 15.0, 15.0)
    b[(k, "CE")][f"{prev} 14:45"] = (80.0, 80.0, 80.0, 80.0); b[(k, "PE")][f"{prev} 14:45"] = (90.0, 90.0, 90.0, 90.0)
    b[(k, "CE")][f"{day} 09:25"] = (70.0, 70.0, 70.0, 70.0); b[(k, "PE")][f"{day} 09:25"] = (80.0, 80.0, 80.0, 80.0)
    return b


SPOT = lambda d, t: 23010.0


def test_twelve_preregistered_hypotheses_with_unique_names():
    assert len(SS.HYPS) == 12 and len({h.name for h in SS.HYPS}) == 12


def test_short_straddle_pnl_gross_cost_and_net():
    h = SS.HYPS[0]
    r = SS.trade(h, "2026-10-13", "2026-10-12", bars_for(), SPOT)
    assert r["atm"] == 23000 and r["entry_prem"] == 200.0 and r["exit_prem"] == 100.0
    assert r["gross"] == 100.0 and 0 < r["cost"] < 10 and abs(r["net"] - (100.0 - r["cost"])) < 1e-9


def test_long_is_the_mirror_of_short_before_costs():
    s = SS.trade(SS.HYPS[0], "2026-10-13", "2026-10-12", bars_for(), SPOT)
    lg = SS.trade(SS.HYPS[4], "2026-10-13", "2026-10-12", bars_for(), SPOT)
    assert lg["gross"] == -s["gross"]


def test_strangle_uses_otm_strikes():
    r = SS.trade(SS.HYPS[8], "2026-10-13", "2026-10-12", bars_for(), SPOT)
    assert r["entry_prem"] == 95.0 and r["exit_prem"] == 35.0 and r["gross"] == 60.0


def test_overnight_hypothesis_enters_the_previous_day():
    r = SS.trade(SS.HYPS[10], "2026-10-13", "2026-10-12", bars_for(), SPOT)
    assert r["entry_prem"] == 170.0 and r["exit_prem"] == 150.0 and r["gross"] == 20.0


def test_missing_bar_or_spot_means_no_trade_never_interpolated():
    b = bars_for()
    del b[(23000, "PE")]["2026-10-13 15:10"]
    assert SS.trade(SS.HYPS[0], "2026-10-13", "2026-10-12", b, SPOT) is None
    assert SS.trade(SS.HYPS[0], "2026-10-13", "2026-10-12", bars_for(), lambda d, t: None) is None
    assert SS.trade(SS.HYPS[10], "2026-10-13", None, bars_for(), SPOT) is None


def test_cost_model_charges_stt_on_the_sold_premium_only():
    short = SS.cost_points("short", 200.0, 100.0)
    long_ = SS.cost_points("long", 200.0, 100.0)
    assert short > long_ and short > 4 * SS.SLIP                        # selling the richer entry premium pays more STT
    assert SS.cost_points("short", 0.0, 0.0) > 4 * SS.SLIP              # flat brokerage + slippage even on a zero-premium trade


def test_student_t_tail_matches_known_values():
    assert abs(SS._t_sf(0.0, 10) - 0.5) < 1e-9
    assert abs(SS._t_sf(2.228, 10) - 0.025) < 1e-3                     # t_{0.975,10} = 2.228
    assert abs(SS._t_sf(-2.228, 10) - 0.975) < 1e-3
    assert abs(SS._t_sf(1.7109, 24) - 0.05) < 1e-3                     # t_{0.95,24} = 1.711


def test_tstat_detects_a_clear_positive_and_ignores_noise():
    mu, t, p = SS.tstat_one_sided([5 + (i % 3) for i in range(40)])
    assert mu > 5 and p < 1e-6
    mu, t, p = SS.tstat_one_sided([(-1) ** i * 10 for i in range(40)])
    assert p > 0.3
    assert SS.tstat_one_sided([1.0, 2.0])[2] == 1.0


def test_carry_exit_uses_the_last_traded_price_only_when_asked_and_counts_it():
    b = bars_for()
    del b[(23000, "PE")]["2026-10-13 15:10"]                     # PE did not trade in the final 5 minutes
    b[(23000, "PE")]["2026-10-13 14:00"] = (55.0, 55.0, 55.0, 55.0)
    assert SS.trade(SS.HYPS[0], "2026-10-13", "2026-10-12", b, SPOT) is None                      # strict mode: no trade
    r = SS.trade(SS.HYPS[0], "2026-10-13", "2026-10-12", b, SPOT, carry_exit=True)
    assert r["carried_legs"] == 1 and r["exit_prem"] == 60.0 + 55.0 and r["gross"] == 200.0 - 115.0
    b2 = bars_for()
    for k in list(b2[(23000, "PE")]):
        if k.startswith("2026-10-13") and k != "2026-10-13 09:20":
            del b2[(23000, "PE")][k]
    assert SS.trade(SS.HYPS[0], "2026-10-13", "2026-10-12", b2, SPOT, carry_exit=True)["carried_legs"] == 1     # still has the 09:20 bar to carry
