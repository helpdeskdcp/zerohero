"""Open-position P&L: ONE canonical valuation (app/arena/positions.py) used by the /live API rows, so the card, the header, the totals and the database-backed settlement agree.
Reproduces the reported case: RELIANCE BUY 1000 sh, fill 1175.10, LTP 1175.20 -> gross +Rs 100; the card showed -Rs 364 because that is NET of ~Rs 464 estimated round-trip charges and the breakdown was not shown."""
import pytest

from app.arena import positions as PN, settle as S

CH = lambda side: (lambda entry, mark, qty: S.equity_cost_rs(side, entry, mark, qty))


def val(side="BUY", qty=1000, entry=1175.10, ltp=1175.20, bid=1175.15, ask=1175.25, age=1.0, **kw):
    return PN.open_valuation(side=side, qty=qty, entry=entry, ltp=ltp, bid=bid, ask=ask, age_s=age, charges_fn=CH(side), **kw)


def test_reported_case_gross_is_plus_100_and_the_card_number_is_net_of_charges():
    v = val()
    assert v["gross_pnl_rs"] == pytest.approx(100.0) and v["mark_price"] == 1175.20 and v["mark_basis"] == "ltp"
    assert v["est_charges_rs"] == pytest.approx(S.equity_cost_rs("BUY", 1175.10, 1175.20, 1000), abs=0.01) and 463.0 < v["est_charges_rs"] < 465.0
    assert v["net_pnl_rs"] == pytest.approx(-363.7, abs=0.1)                                                     # the -Rs 364 of the screenshot = gross +100 - charges 463.8
    assert v["net_pnl_rs"] == pytest.approx(v["gross_pnl_rs"] - v["est_charges_rs"], abs=0.01)                   # identity: charges are deducted exactly once


@pytest.mark.parametrize("side,entry,ltp,gross", [("BUY", 1175.10, 1175.20, 100.0), ("BUY", 1175.10, 1174.90, -200.0), ("SELL", 1175.10, 1175.00, 100.0), ("SELL", 1175.10, 1175.40, -300.0)])
def test_gross_formula_for_buy_and_sell_with_quantity_1000(side, entry, ltp, gross):
    assert val(side=side, entry=entry, ltp=ltp)["gross_pnl_rs"] == pytest.approx(gross)


def test_quantity_one_and_the_open_quantity_after_a_partial_exit():
    assert val(qty=1)["gross_pnl_rs"] == pytest.approx(0.1)
    full, part = val(qty=1000), val(qty=400)                                                                      # 600 already exited: only the OPEN quantity is valued and charged
    assert part["gross_pnl_rs"] == pytest.approx(40.0) and part["est_charges_rs"] < full["est_charges_rs"] and part["est_charges_rs"] == pytest.approx(S.equity_cost_rs("BUY", 1175.10, 1175.20, 400), abs=0.01)


def test_duplicate_ticks_do_not_accumulate():
    a = val(); b = val(); c = val(); assert a == b == c                                                           # same input, same output: nothing is summed over repeated events


def test_exit_now_uses_the_side_the_exchange_would_fill():
    v = val(side="BUY", ltp=1175.20, bid=1175.05, ask=1175.25); assert v["exit_now_price"] == 1175.05 and v["exit_now_gross_rs"] == pytest.approx(-50.0) and v["exit_now_net_rs"] < v["net_pnl_rs"]
    s = val(side="SELL", entry=1175.10, ltp=1175.00, bid=1174.95, ask=1175.05); assert s["exit_now_price"] == 1175.05 and s["exit_now_gross_rs"] == pytest.approx(50.0)


def test_stale_or_missing_prices_never_produce_a_pnl():
    for kw in ({"age": 120.0}, {"ltp": None, "bid": None, "ask": None}, {"ltp": 0, "bid": 0, "ask": 0}):
        v = val(**kw); assert v["net_pnl_rs"] is None and v["gross_pnl_rs"] is None and v["est_charges_rs"] is None
    assert val(age=120.0)["stale"] is True and val(age=2.0)["stale"] is False
    v = val(ltp=None, bid=1175.15, ask=1175.25); assert v["mark_basis"] == "bid" and v["mark_price"] == 1175.15                  # no LTP: the side that would close the position, and it says so


def test_option_basis_marks_at_the_bid_like_before():
    ch = lambda e, m, n: 100.0
    v = PN.open_valuation(side="BUY", qty=2, entry=100.0, ltp=110.0, bid=108.0, ask=112.0, age_s=2.0, charges_fn=ch, unit=75, basis="bid")
    assert v["mark_price"] == 108.0 and v["mark_basis"] == "bid" and v["gross_pnl_rs"] == pytest.approx(8.0 * 75 * 2) and v["net_pnl_rs"] == pytest.approx(1200.0 - 100.0)
