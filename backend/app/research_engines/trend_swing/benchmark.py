"""Baselines the E1 engine must beat: buy-and-hold, a simple long-only EMA
cross, and a simple long+short Donchian breakout. Same daily bars, same 1pt
cost + 1pt slippage, same next-open fills, no look-ahead. R is normalised to
2% of price so PF / expectancy are comparable across strategies."""
from __future__ import annotations

from . import indicators as I


def _state_series(bars, cfg, kind: str) -> list[int]:
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    c = [b["c"] for b in bars]
    if kind == "ema_cross":
        ef = I.ema(c, cfg["bench_ema_fast"])
        es = I.ema(c, cfg["bench_ema_slow"])
        return [1 if (ef[i] is not None and es[i] is not None and ef[i] > es[i]) else 0
                for i in range(len(c))]
    n = cfg["bench_donchian_n"]
    hi, lo = I.donchian(h, l, n)
    out, s = [0] * len(c), 0
    for i in range(len(c)):
        if hi[i] is not None and c[i] > hi[i]:
            s = 1
        elif lo[i] is not None and c[i] < lo[i]:
            s = -1
        out[i] = s
    return out


def _run_state(bars, cfg, state: list[int], warm: int) -> dict:
    slip, cost = cfg["slippage_points"], cfg["cost_points"]
    n = len(bars)
    pos = 0
    entry = 0.0
    entry_d = ""
    entry_i = 0
    ref = 1.0
    prev = None
    trades, daily_R = [], []
    for i in range(warm, n):
        b = bars[i]
        dr = 0.0
        if pos != 0 and state[i] != pos and i < n - 1:
            ex = bars[i + 1]["o"] - (slip if pos > 0 else -slip)
            dr += (ex - (prev if prev is not None else entry)) * pos / ref
            net = (ex - entry) * pos - cost
            trades.append({"entry_date": entry_d, "exit_date": bars[i + 1]["date"],
                           "direction": "LONG" if pos > 0 else "SHORT",
                           "points": round(net, 2), "r_multiple": round(net / ref, 4),
                           "risk_points": round(ref, 2), "sl_rate": 0.0,
                           "year": entry_d[:4], "held_days": (i + 1) - entry_i,
                           "exit_reason": "STATE_FLIP", "mfe_R": 0.0})
            pos, prev = 0, None
        if pos != 0:
            dr += (b["c"] - (prev if prev is not None else entry)) * pos / ref
            prev = b["c"]
        if pos == 0 and state[i] != 0 and i < n - 1:
            nb = bars[i + 1]
            ref = max(1.0, 0.02 * nb["o"])
            entry = nb["o"] + (slip if state[i] > 0 else -slip)
            pos, entry_d, entry_i, prev = state[i], b["date"], i + 1, None
        daily_R.append({"date": b["date"], "r": round(dr, 6), "in_pos": 1 if pos else 0})
    if pos != 0:
        net = (bars[-1]["c"] - entry) * pos - cost
        trades.append({"entry_date": entry_d, "exit_date": bars[-1]["date"],
                       "direction": "LONG" if pos > 0 else "SHORT", "points": round(net, 2),
                       "r_multiple": round(net / ref, 4), "risk_points": round(ref, 2),
                       "sl_rate": 0.0, "year": entry_d[:4], "held_days": (n - 1) - entry_i,
                       "exit_reason": "EOD", "mfe_R": 0.0})
    return {"trades": trades, "daily_R": daily_R}


def _buy_hold_daily_R(bars, warm) -> list[dict]:
    out = []
    for i in range(warm, len(bars)):
        b = bars[i]
        if i == warm:
            out.append({"date": b["date"], "r": 0.0, "in_pos": 1})
            continue
        ref = max(1.0, 0.02 * bars[i - 1]["c"])
        out.append({"date": b["date"],
                    "r": round((b["c"] - bars[i - 1]["c"]) / ref, 6), "in_pos": 1})
    return out


def run(bars, cfg) -> dict:
    warm = cfg["warmup_days"]
    return {
        "buy_and_hold": {"daily_R": _buy_hold_daily_R(bars, warm), "trades": []},
        "ema_cross": _run_state(bars, cfg, _state_series(bars, cfg, "ema_cross"), warm),
        "donchian": _run_state(bars, cfg, _state_series(bars, cfg, "donchian"), warm),
    }
