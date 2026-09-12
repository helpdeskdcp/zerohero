"""
app/institutional_edge/ev.py -- Gross/Net/Risk-adjusted EV. Pure in-memory
rows, no DB.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.institutional_edge.ev import compute_ev  # noqa: E402


def _rows(n, *, win_rate=0.5, points_win=10.0, points_loss=-8.0, start=0):
    out = []
    for i in range(n):
        is_win = ((start + i) % 10) < round(win_rate * 10)
        out.append({"outcome": "WIN" if is_win else "LOSS",
                    "points": points_win if is_win else points_loss})
    return out


def test_gross_ev_matches_the_textbook_formula():
    rows = _rows(60, win_rate=0.6, points_win=10.0, points_loss=-8.0)
    r = compute_ev(rows, exchange="MCX", segment="NATURALGAS_OPTION")
    assert r.status == "OK"
    expected_gross = 0.6 * 10.0 - 0.4 * 8.0
    assert abs(r.gross_ev_points - expected_gross) < 1e-6


def test_net_ev_subtracts_the_itemized_cost_in_points():
    rows = _rows(60, win_rate=0.6, points_win=10.0, points_loss=-8.0)
    r = compute_ev(rows, exchange="MCX", segment="NATURALGAS_OPTION")
    expected_cost_points = 113.50 / 1250
    assert abs(r.cost_points - expected_cost_points) < 1e-6
    assert abs(r.net_ev_points - (r.gross_ev_points - expected_cost_points)) < 1e-6


def test_risk_adjusted_ev_normalizes_by_average_loss_magnitude():
    rows = _rows(60, win_rate=0.6, points_win=10.0, points_loss=-8.0)
    r = compute_ev(rows, exchange="MCX", segment="NATURALGAS_OPTION")
    # both sides independently rounded to 4dp, so compare with a tolerance
    # that accounts for that rather than expecting bit-exact equality
    assert abs(r.risk_adjusted_ev - (r.net_ev_points / 8.0)) < 1e-3


def test_uncalibrated_instrument_still_reports_gross_ev_but_not_net():
    rows = _rows(60, win_rate=0.6, points_win=10.0, points_loss=-8.0)
    r = compute_ev(rows, exchange="NSE", segment="NIFTY_OPTION")
    assert r.status == "UNCALIBRATED_COST"
    assert r.gross_ev_points is not None
    assert r.net_ev_points is None
    assert r.cost_points is None
    assert r.cost_note is not None


def test_insufficient_sample_reports_nothing_computed():
    r = compute_ev(_rows(10), exchange="MCX", segment="NATURALGAS_OPTION")
    assert r.status == "INSUFFICIENT_DATA"
    assert r.gross_ev_points is None


def test_slippage_increases_the_cost_and_lowers_net_ev():
    rows = _rows(60, win_rate=0.6, points_win=10.0, points_loss=-8.0)
    r_no_slip = compute_ev(rows, exchange="MCX", segment="NATURALGAS_OPTION")
    r_slip = compute_ev(rows, exchange="MCX", segment="NATURALGAS_OPTION", slippage_points=0.05)
    assert r_slip.cost_points > r_no_slip.cost_points
    assert r_slip.net_ev_points < r_no_slip.net_ev_points


def test_all_losses_never_crashes_on_zero_avg_loss_edge_case():
    rows = _rows(60, win_rate=1.0, points_win=10.0, points_loss=-8.0)   # all wins -> avg_loss = 0
    r = compute_ev(rows, exchange="MCX", segment="NATURALGAS_OPTION")
    assert r.status == "OK"
    assert r.risk_adjusted_ev is None   # can't normalize by a zero average loss
