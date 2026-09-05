"""
Order-flow smart-money backtest aggregation.

The engine (smart_money.py) is tested elsewhere; this covers backtest.py's job
-- walking every captured session, turning resolved setups into trades, and
aggregating W/L, gross win points, gross SL-hit points, net, expectancy, PF,
max drawdown, and the reliability gate.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import app.orderflow.backtest as BT   # noqa: E402


def _leg(side, entry, stop, target, status):
    risk = abs(entry - stop)
    reward = abs(target - entry)
    return {"side": side, "entry": entry, "stop_loss": stop, "target": target,
            "risk_points": risk, "reward_points": reward,
            "rr": round(reward / risk, 2) if risk else None,
            "breakout_bar": None if status == "PENDING" else f"{side}-bo",
            "outcome": {"status": status, "resolved_bar": None if status in ("PENDING", "TRIGGERED") else f"{side}-res"}}


def _spike(bs, buy_status, sell_status, *, h=110, l=100):
    return {"candle": {"bar_start": bs, "o": l, "h": h, "l": l, "c": (h + l) / 2, "v": 1000},
            "volume_x_avg": 3.0, "range_points": h - l,
            "buy": _leg("BUY", h, l, h + 3 * (h - l), buy_status),
            "sell": _leg("SELL", l, h, l - 3 * (h - l), sell_status)}


def _wire(monkeypatch, sessions_map):
    """sessions_map: {date: smart_money_setups-result}."""
    dates = list(sessions_map.keys())
    monkeypatch.setattr(BT.market_hub, "session_dates", lambda sym, tf="5m", limit=400: list(reversed(dates)))
    monkeypatch.setattr(BT.market_hub, "session_bars",
                        lambda sym, d, tf="5m": [{"h": 1, "l": 1}] if d in sessions_map else [])
    monkeypatch.setattr(BT._sm, "smart_money_setups",
                        lambda bars, **kw: sessions_map[_wire._cur] if _wire._cur in sessions_map else {"status": "NO_DATA"})
    # smart_money_setups is called once per session; thread the current date through
    orig_bars = BT.market_hub.session_bars
    def _bars(sym, d, tf="5m"):
        _wire._cur = d
        return [{"h": 1, "l": 1}] if d in sessions_map else []
    monkeypatch.setattr(BT.market_hub, "session_bars", _bars)
_wire._cur = None


def test_win_loss_points_and_net(monkeypatch):
    # one session, H110 L100 -> risk 10, reward 30.
    # BUY TARGET_HIT (+30), SELL STOP_HIT (-10)  -> net +20
    _wire(monkeypatch, {"2026-09-04": {"status": "OK", "setups": [
        _spike("2026-09-04T04:00:00Z", "TARGET_HIT", "STOP_HIT")]}})
    bt = BT.backtest("NIFTY")
    o = bt["overall"]
    assert bt["status"] == "OK"
    assert o["signals"] == 2 and o["wins"] == 1 and o["losses"] == 1 and o["open"] == 0
    assert o["gross_win_points"] == 30.0
    assert o["gross_loss_points"] == 10.0        # SL-hit magnitude, positive
    assert o["net_points"] == 20.0
    assert o["avg_win_points"] == 30.0 and o["avg_loss_points"] == 10.0
    assert o["win_rate"] == 0.5
    assert o["expectancy_points"] == 10.0        # 20 / 2 resolved
    assert o["profit_factor"] == 3.0
    assert o["breakeven_win_rate"] == 0.25       # 1/(1+3)


def test_triggered_is_open_not_counted_in_realized(monkeypatch):
    _wire(monkeypatch, {"2026-09-04": {"status": "OK", "setups": [
        _spike("2026-09-04T04:00:00Z", "TRIGGERED", "PENDING")]}})
    bt = BT.backtest("NIFTY")
    o = bt["overall"]
    assert o["signals"] == 1 and o["open"] == 1 and o["wins"] == 0 and o["losses"] == 0
    assert o["win_rate"] is None and o["expectancy_points"] is None
    assert bt["trades"][0]["result"] == "OPEN" and bt["trades"][0]["points"] == 0.0


def test_reliability_gate_needs_trades_and_sessions(monkeypatch):
    # 2 sessions, plenty of resolved trades but only 2 distinct sessions -> not reliable
    sm = {"status": "OK", "setups": [_spike(f"d{i}", "STOP_HIT", "STOP_HIT") for i in range(15)]}
    _wire(monkeypatch, {"2026-09-03": sm, "2026-09-04": sm})
    bt = BT.backtest("NIFTY")
    o = bt["overall"]
    assert o["resolved"] >= 20
    assert o["reliable"] is False
    assert "distinct sessions" in o["reliability_reason"]


def test_reliable_when_enough_trades_and_sessions(monkeypatch):
    sm = {"status": "OK", "setups": [_spike("x", "TARGET_HIT", "STOP_HIT")]}
    smap = {f"2026-08-{d:02d}": sm for d in range(1, 13)}   # 12 sessions, 24 resolved
    _wire(monkeypatch, smap)
    bt = BT.backtest("NIFTY")
    o = bt["overall"]
    assert o["resolved"] == 24 and bt["traded_sessions"] == 12
    assert o["reliable"] is True and o["reliability_reason"] is None


def test_by_side_split(monkeypatch):
    _wire(monkeypatch, {"2026-09-04": {"status": "OK", "setups": [
        _spike("t1", "TARGET_HIT", "STOP_HIT"),
        _spike("t2", "STOP_HIT", "TARGET_HIT")]}})
    bt = BT.backtest("NIFTY")
    assert bt["by_side"]["BUY"]["wins"] == 1 and bt["by_side"]["BUY"]["losses"] == 1
    assert bt["by_side"]["SELL"]["wins"] == 1 and bt["by_side"]["SELL"]["losses"] == 1


def test_max_drawdown(monkeypatch):
    # sequence of points: +30, -10, -10, -10  -> equity 30,20,10,0 ; peak 30 ; dd -30
    setups = [_spike("t0", "TARGET_HIT", "PENDING"),
              _spike("t1", "STOP_HIT", "PENDING"),
              _spike("t2", "STOP_HIT", "PENDING"),
              _spike("t3", "STOP_HIT", "PENDING")]
    _wire(monkeypatch, {"2026-09-04": {"status": "OK", "setups": setups}})
    bt = BT.backtest("NIFTY")
    assert bt["overall"]["max_drawdown_points"] == -30.0


def test_no_signals(monkeypatch):
    _wire(monkeypatch, {"2026-09-04": {"status": "OK", "setups": [
        _spike("t0", "PENDING", "PENDING")]}})
    bt = BT.backtest("NIFTY")
    assert bt["status"] == "NO_SIGNALS"


def test_trade_row_carries_exit_price(monkeypatch):
    _wire(monkeypatch, {"2026-09-04": {"status": "OK", "setups": [
        _spike("t0", "TARGET_HIT", "STOP_HIT", h=110, l=100)]}})
    bt = BT.backtest("NIFTY")
    by_res = {t["result"]: t for t in bt["trades"]}
    assert by_res["WIN"]["exit_price"] == 140      # target = 110 + 3*10
    assert by_res["WIN"]["points"] == 30
    assert by_res["LOSS"]["exit_price"] == 110     # SELL stop = spike high
    assert by_res["LOSS"]["points"] == -10
