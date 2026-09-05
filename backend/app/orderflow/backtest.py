"""
Order-flow smart-money backtest -- runs the volume-spike breakout engine over
EVERY captured session for a symbol and aggregates the outcomes.

Each broken-out setup (outcome != PENDING) is one trade:
  * TARGET_HIT  -> win,  points = +reward_points  (= rr x risk)
  * STOP_HIT    -> loss, points = -risk_points
  * TRIGGERED   -> still open at that session's close; NOT counted in realized
                   P&L, reported separately as `open`.

Everything is read-only, from captured ~5m OHLCV bars. Same bar-granularity
caveat as the live engine: a bar that spans both target and stop is scored
STOP_HIT (pessimistic). Sample size is small until weeks of capture exist --
`reliable` is False below MIN_SAMPLE resolved trades and no edge should be
claimed from the numbers before then.
"""
from __future__ import annotations

from .. import market_hub
from . import smart_money as _sm

MIN_SAMPLE = 20          # resolved trades, matches analyze_holdtime.py / calibration_report
MIN_SESSIONS = 10        # distinct trading days -- intra-day signals are highly
                         # correlated, so raw trade count alone overstates the sample


def _equity_max_drawdown(points_seq: list) -> float:
    """Peak-to-trough of the running cumulative-points curve (<= 0)."""
    peak = 0.0
    cum = 0.0
    dd = 0.0
    for p in points_seq:
        cum += p
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return round(dd, 4)


def _agg(trades: list) -> dict:
    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    opens = [t for t in trades if t["result"] == "OPEN"]
    resolved = wins + losses
    gross_win = round(sum(t["points"] for t in wins), 4)
    gross_loss = round(sum(-t["points"] for t in losses), 4)   # positive magnitude
    net = round(gross_win - gross_loss, 4)
    n_res = len(resolved)
    return {
        "signals": len(trades),
        "wins": len(wins), "losses": len(losses), "open": len(opens),
        "win_rate": round(len(wins) / n_res, 4) if n_res else None,
        "gross_win_points": gross_win,
        "gross_loss_points": gross_loss,
        "net_points": net,
        "avg_win_points": round(gross_win / len(wins), 4) if wins else None,
        "avg_loss_points": round(gross_loss / len(losses), 4) if losses else None,
        "expectancy_points": round(net / n_res, 4) if n_res else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "max_drawdown_points": _equity_max_drawdown(
            [t["points"] for t in sorted(resolved, key=lambda x: (x["session"], x["candle_ts"]))]),
        "resolved": n_res,
        "min_sample": MIN_SAMPLE,
    }


def _trade_from_leg(session: str, spike: dict, side: str) -> dict | None:
    leg = spike.get(side) or {}
    status = (leg.get("outcome") or {}).get("status")
    if status in (None, "PENDING"):
        return None
    if status == "TARGET_HIT":
        result, points, exit_price = "WIN", leg["reward_points"], leg["target"]
    elif status == "STOP_HIT":
        result, points, exit_price = "LOSS", -leg["risk_points"], leg["stop_loss"]
    else:                                   # TRIGGERED
        result, points, exit_price = "OPEN", 0.0, None
    return {
        "session": session,
        "candle_ts": spike["candle"]["bar_start"],
        "side": leg["side"],
        "entry": leg["entry"], "stop_loss": leg["stop_loss"], "target": leg["target"],
        "risk_points": leg["risk_points"], "reward_points": leg["reward_points"],
        "rr": leg["rr"],
        "breakout_bar": leg.get("breakout_bar"),
        "resolved_bar": (leg.get("outcome") or {}).get("resolved_bar"),
        "result": result,
        "points": round(points, 4),
        "exit_price": exit_price,          # the winning target price, or the SL-hit price
        "volume_x_avg": spike.get("volume_x_avg"),
    }


def backtest(symbol: str, *, tf: str = "5m", volume_mult: float = 2.0, rr: float = 3.0,
             sessions: int | list | None = None) -> dict:
    """`sessions`: None -> all captured; an int -> that many most-recent; a list
    -> exactly those IST dates."""
    sym = symbol.upper()
    if isinstance(sessions, list):
        dates = [str(d).strip() for d in sessions if str(d).strip()]
    else:
        all_dates = market_hub.session_dates(sym, tf=tf, limit=400)
        dates = all_dates[:sessions] if isinstance(sessions, int) and sessions > 0 else all_dates
    dates = sorted(set(dates))            # chronological

    trades: list = []
    per_session: dict = {}
    scanned = 0
    for d in dates:
        bars = market_hub.session_bars(sym, d, tf=tf)
        if not bars:
            continue
        scanned += 1
        sm = _sm.smart_money_setups(bars, volume_mult=volume_mult, rr=rr)
        if sm.get("status") != "OK":
            continue
        s_trades = []
        for spike in sm.get("setups", []):
            for side in ("buy", "sell"):
                t = _trade_from_leg(d, spike, side)
                if t:
                    s_trades.append(t)
        trades.extend(s_trades)
        if s_trades:
            per_session[d] = _agg(s_trades)

    if not trades:
        return {"status": "NO_SIGNALS", "symbol": sym, "sessions_scanned": scanned,
                "sessions": dates, "volume_mult": volume_mult, "rr": rr,
                "note": "no volume-spike breakout hit target or stop in the captured sessions"}

    trades.sort(key=lambda t: (t["session"], t["candle_ts"], t["side"]))
    overall = _agg(trades)
    by_side = {sd: _agg([t for t in trades if t["side"] == sd]) for sd in ("BUY", "SELL")}

    n_traded_sessions = len({t["session"] for t in trades})
    reliable = (overall["resolved"] >= MIN_SAMPLE) and (n_traded_sessions >= MIN_SESSIONS)
    reason = None
    if not reliable:
        bits = []
        if overall["resolved"] < MIN_SAMPLE:
            bits.append(f"{overall['resolved']}/{MIN_SAMPLE} resolved trades")
        if n_traded_sessions < MIN_SESSIONS:
            bits.append(f"{n_traded_sessions}/{MIN_SESSIONS} distinct sessions")
        reason = "INSUFFICIENT SAMPLE: " + ", ".join(bits) + " -- descriptive only, no edge claim"
    overall["reliable"] = reliable
    overall["reliability_reason"] = reason
    # a 1:rr trade needs win_rate > 1/(1+rr) just to break even
    overall["breakeven_win_rate"] = round(1.0 / (1.0 + rr), 4)

    return {
        "status": "OK",
        "method": "OHLCV_BARS",
        "note": ("volume-spike breakout; entry/target/stop judged at ~5m bar "
                 "granularity; a bar spanning both -> STOP_HIT (pessimistic). "
                 f"Needs >= {MIN_SAMPLE} resolved trades across >= {MIN_SESSIONS} "
                 "sessions before any edge claim; intra-day signals are correlated."),
        "symbol": sym, "tf": tf, "volume_mult": volume_mult, "rr": rr,
        "sessions_scanned": scanned, "traded_sessions": n_traded_sessions,
        "sessions": dates,
        "overall": overall,
        "by_side": by_side,
        "by_session": per_session,
        "trades": trades,
    }
