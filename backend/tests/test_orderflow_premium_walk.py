"""
Premium-basis re-walk (app/orderflow/premium_walk.py) + backtest basis="premium".

The engine builds the setup in INDEX points; premium_walk re-prices a resolved
leg on the captured ATM option (BUY -> long CE, SELL -> long PE), keeping the
index level as the exit trigger and taking WIN/LOSS from the premium-P&L sign.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.orderflow import premium_walk as PW   # noqa: E402
import app.orderflow.backtest as BT            # noqa: E402


# ---------------------------------------------------------------- premium_walk
def _series(*pairs):
    return list(pairs)


def test_asof_returns_last_at_or_before():
    s = _series(("a", 1.0), ("m", 2.0), ("z", 3.0))
    assert PW._asof(s, "a") == 1.0
    assert PW._asof(s, "n") == 2.0
    assert PW._asof(s, "zzz") == 3.0
    assert PW._asof(s, "0") is None       # before the first tick
    assert PW._asof([], "x") is None


def test_rewalk_buy_uses_ce_nearest_strike_and_prices_pnl():
    opt = {
        (100.0, "CE"): _series(("t-bo", 20.0), ("t-mid", 35.0), ("t-res", 30.0)),
        (110.0, "CE"): _series(("t-bo", 12.0), ("t-mid", 25.0), ("t-res", 22.0)),
        (100.0, "PE"): _series(("t-bo", 9.0), ("t-res", 9.0)),
    }
    # index entry 108 -> ATM CE = strike 110
    rw = PW.rewalk_leg(opt, entry_price=108.0, side="BUY", entry_ts="t-bo", exit_ts="t-res")
    assert rw["basis"] == "PREMIUM"
    assert rw["strike"] == 110.0 and rw["option_type"] == "CE"
    assert rw["premium_entry"] == 12.0 and rw["premium_exit"] == 22.0
    assert rw["premium_points"] == 10.0                 # 22 - 12, a WIN by sign
    assert rw["premium_mfe"] == 13.0                    # max 25 - entry 12
    assert rw["premium_mae"] == 0.0                     # never below entry
    assert rw["premium_ticks"] == 3 and rw["premium_thin"] is False


def test_rewalk_sell_uses_pe_and_can_lose():
    opt = {(100.0, "PE"): _series(("t-bo", 40.0), ("t-lo", 15.0), ("t-res", 18.0))}
    rw = PW.rewalk_leg(opt, entry_price=101.0, side="SELL", entry_ts="t-bo", exit_ts="t-res")
    assert rw["option_type"] == "PE"
    assert rw["premium_points"] == -22.0                # 18 - 40
    assert rw["premium_mae"] == -25.0                   # 15 - 40


def test_rewalk_none_when_no_series_or_bad_entry():
    assert PW.rewalk_leg({}, entry_price=100, side="BUY", entry_ts="a", exit_ts="b") is None
    # entry ts before the first captured tick -> no entry price
    opt = {(100.0, "CE"): _series(("m", 5.0), ("z", 8.0))}
    assert PW.rewalk_leg(opt, entry_price=100, side="BUY", entry_ts="a", exit_ts="z") is None
    # zero/negative entry premium is unusable
    opt2 = {(100.0, "CE"): _series(("a", 0.0), ("z", 8.0))}
    assert PW.rewalk_leg(opt2, entry_price=100, side="BUY", entry_ts="a", exit_ts="z") is None
    assert PW.rewalk_leg(opt, entry_price=100, side="BUY", entry_ts=None, exit_ts="z") is None


def test_rewalk_thin_flag_when_two_or_fewer_ticks():
    opt = {(100.0, "CE"): _series(("t-bo", 10.0), ("t-res", 14.0))}
    rw = PW.rewalk_leg(opt, entry_price=100, side="BUY", entry_ts="t-bo", exit_ts="t-res")
    assert rw["premium_ticks"] == 2 and rw["premium_thin"] is True
    assert rw["premium_points"] == 4.0


# ---------------------------------------------------------------- backtest basis
def _leg(side, entry, stop, target, status):
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if status == "TARGET_HIT":
        pts, xp = round(reward, 4), target
    elif status == "STOP_HIT":
        pts, xp = round(-risk, 4), stop
    else:
        pts, xp = 0.0, None
    return {"side": side, "entry": entry, "stop_loss": stop, "target": target,
            "risk_points": risk, "reward_points": reward,
            "rr": round(reward / risk, 2) if risk else None,
            "breakout_bar": None if status == "PENDING" else f"{side}-bo",
            "outcome": {"status": status, "points": pts, "exit_price": xp,
                        "resolved_bar": None if status in ("PENDING", "TRIGGERED") else f"{side}-res"}}


def _spike(bs, buy_status, sell_status, *, h=110, l=100):
    return {"candle": {"bar_start": bs, "o": l, "h": h, "l": l, "c": (h + l) / 2, "v": 1000},
            "volume_x_avg": 3.0, "range_points": h - l,
            "buy": _leg("BUY", h, l, h + 3 * (h - l), buy_status),
            "sell": _leg("SELL", l, h, l - 3 * (h - l), sell_status)}


def _wire_premium(monkeypatch, setups, opt_map):
    monkeypatch.setattr(BT.market_hub, "session_dates", lambda s, tf="5m", limit=400: ["2026-09-04"])
    monkeypatch.setattr(BT.market_hub, "session_bars",
                        lambda s, d, tf="5m": [{"h": 1, "l": 1}] if d == "2026-09-04" else [])
    monkeypatch.setattr(BT._sm, "smart_money_setups",
                        lambda bars, **kw: {"status": "OK", "setups": setups})
    monkeypatch.setattr(BT.market_hub, "session_option_quotes", lambda s, d: opt_map)


def test_backtest_premium_basis_reprices_and_flags_coverage(monkeypatch):
    # BUY index TARGET_HIT (+30 index) but the CE only ran +6 -> still a WIN, smaller
    # SELL index STOP_HIT (-10 index) but the PE actually rose +4 -> a WIN in premium
    opt = {
        (110.0, "CE"): [("BUY-bo", 40.0), ("BUY-mid", 55.0), ("BUY-res", 46.0)],
        (100.0, "PE"): [("SELL-bo", 30.0), ("SELL-mid", 41.0), ("SELL-res", 34.0)],
    }
    _wire_premium(monkeypatch, [_spike("2026-09-04T04:00:00Z", "TARGET_HIT", "STOP_HIT")], opt)
    bt = BT.backtest("NIFTY", basis="premium")
    assert bt["basis"] == "premium"
    by = {t["side"]: t for t in bt["trades"]}
    assert by["BUY"]["basis"] == "PREMIUM"
    assert by["BUY"]["points"] == 6.0 and by["BUY"]["result"] == "WIN"
    assert by["BUY"]["index_points"] == 30.0 and by["BUY"]["index_status"] == "TARGET_HIT"
    assert by["SELL"]["points"] == 4.0 and by["SELL"]["result"] == "WIN"   # -10 index -> +4 premium
    cov = bt["basis_coverage"]
    assert cov["premium_repriced"] == 2 and cov["index_fallback"] == 0
    assert cov["premium_coverage"] == 1.0
    assert bt["overall"]["net_points"] == 10.0        # 6 + 4, premium space


def test_backtest_premium_falls_back_to_index_when_no_option_series(monkeypatch):
    _wire_premium(monkeypatch, [_spike("2026-09-04T04:00:00Z", "TARGET_HIT", "STOP_HIT")], {})
    bt = BT.backtest("NIFTY", basis="premium")
    for t in bt["trades"]:
        assert t["basis"] == "INDEX_FALLBACK"
    # kept the index-space points
    assert bt["overall"]["net_points"] == 20.0        # +30 -10
    assert bt["basis_coverage"]["index_fallback"] == 2
    assert bt["basis_coverage"]["premium_repriced"] == 0


def test_backtest_index_basis_unchanged_and_default(monkeypatch):
    _wire_premium(monkeypatch, [_spike("2026-09-04T04:00:00Z", "TARGET_HIT", "STOP_HIT")], {})
    bt = BT.backtest("NIFTY")                         # default basis
    assert bt["basis"] == "index"
    for t in bt["trades"]:
        assert t["basis"] == "INDEX"
    assert bt["overall"]["net_points"] == 20.0
