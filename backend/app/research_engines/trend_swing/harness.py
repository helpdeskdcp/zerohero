"""
Daily SWING simulator. Positions are held ACROSS DAYS. Entry at the NEXT bar's
open (no look-ahead). Exit = intrabar ATR/chandelier/Donchian trailing stop
(checked first, pessimistic) OR a confirmed trend-flip (next open) OR a hold
cap. Vol-normalised: every trade risks 1R. 1 pt cost + 1 pt slippage.

Returns per-trade records AND a daily mark-to-market R series (for annualised
Sharpe / equity-curve drawdown / exposure).
"""
from __future__ import annotations

from .engine import build, target_direction


def _sidestr(d: int) -> str:
    return "LONG" if d > 0 else "SHORT"


def simulate(bars: list[dict], cfg: dict, *, side_filter: str | None = None,
             frame: list[dict] | None = None, tgt: list[int] | None = None) -> dict:
    frame = frame if frame is not None else build(bars, cfg)
    tgt = tgt if tgt is not None else target_direction(frame, cfg)
    n = len(frame)
    warm = cfg["warmup_days"]
    slip = cfg["slippage_points"]
    cost = cfg["cost_points"]
    trail_mode = cfg["trail_mode"]
    tmult = cfg["trail_atr_mult"]
    smult = cfg["sl_atr_mult"]
    dexit = cfg["donchian_exit_n"]
    cool = cfg["cooldown_days"]
    maxhold = cfg["max_hold_days"]

    pos = 0
    entry_px = entry_i = risk = hi_water = 0.0
    entry_row = None
    stop = 0.0
    held = 0
    cooldown_until = -1
    pending = None                     # ("FLIP" | "MAXHOLD")

    trades: list[dict] = []
    daily_R: list[dict] = []           # {date, r}
    prev_mark = None

    for i in range(warm, n):
        r = frame[i]
        day_r = 0.0

        # ---- execute a deferred next-open exit ----
        if pos != 0 and pending is not None:
            exit_px = r["o"] - (slip if pos > 0 else -slip)      # adverse
            day_r += (exit_px - (prev_mark if prev_mark is not None else entry_px)) * pos / risk
            _close(trades, entry_row, entry_px, exit_px, pos, risk, held, hi_water,
                   pending, cost, r["date"])
            pos, pending, prev_mark = 0, None, None
            cooldown_until = i + cool

        # ---- manage an open position on bar i ----
        if pos != 0:
            held += 1
            hi_water = max(hi_water, r["h"]) if pos > 0 else min(hi_water, r["l"])
            atr = r["atr"] or 0.0
            if trail_mode == "chandelier" and atr > 0:
                t = hi_water - tmult * atr if pos > 0 else hi_water + tmult * atr
                stop = max(stop, t) if pos > 0 else min(stop, t)
            elif trail_mode == "donchian":
                a = max(0, i - dexit)
                dl = min(x["l"] for x in frame[a:i]) if i > a else r["l"]
                dh = max(x["h"] for x in frame[a:i]) if i > a else r["h"]
                stop = max(stop, dl) if pos > 0 else min(stop, dh)

            hit = (r["l"] <= stop) if pos > 0 else (r["h"] >= stop)
            if hit:
                exit_px = stop
                day_r += (exit_px - (prev_mark if prev_mark is not None else entry_px)) * pos / risk
                _close(trades, entry_row, entry_px, exit_px, pos, risk, held, hi_water,
                       "STOP" if held <= 1 or hi_water == entry_px else "TRAIL", cost, r["date"])
                pos, prev_mark = 0, None
                cooldown_until = i + cool
            else:
                # mark to market at today's close
                day_r += (r["c"] - (prev_mark if prev_mark is not None else entry_px)) * pos / risk
                prev_mark = r["c"]
                if cfg["exit_on_trend_flip"] and tgt[i] != 0 and tgt[i] != pos:
                    pending = "FLIP"
                elif maxhold and held >= maxhold:
                    pending = "MAXHOLD"

        # ---- entry decision from bar i, fill at i+1 open ----
        if pos == 0 and pending is None and i < n - 1 and i >= cooldown_until:
            d = tgt[i]
            if d != 0 and (side_filter is None or _sidestr(d) == side_filter):
                nb = frame[i + 1]
                atr = r["atr"] or 0.0
                if atr > 0:
                    entry_px = nb["o"] + (slip if d > 0 else -slip)
                    risk = smult * atr
                    stop = entry_px - d * risk
                    hi_water = entry_px
                    pos, entry_i, entry_row, held = d, i + 1, r, 0
                    prev_mark = None

        daily_R.append({"date": r["date"], "r": round(day_r, 6),
                        "in_pos": 1 if pos != 0 else 0,
                        "regime": None})
    # close any dangling position at the last close
    if pos != 0:
        _close(trades, entry_row, entry_px, frame[-1]["c"], pos, risk, held, hi_water,
               "EOD", cost, frame[-1]["date"])
    return {"trades": trades, "daily_R": daily_R}


def _close(trades, erow, entry_px, exit_px, pos, risk, held, hi_water, reason, cost, exit_date):
    gross = (exit_px - entry_px) * pos
    net = gross - cost
    rmult = net / risk if risk else 0.0
    mfe = ((hi_water - entry_px) * pos) / risk if risk else 0.0
    trades.append({
        "entry_date": erow["date"], "exit_date": exit_date,
        "direction": _sidestr(pos),
        "entry": round(entry_px, 2), "exit": round(exit_px, 2),
        "risk_points": round(risk, 2), "points": round(net, 2),
        "r_multiple": round(rmult, 4), "mfe_R": round(max(0.0, mfe), 3),
        "held_days": held, "exit_reason": reason,
        "sl_rate": 1.0 if reason == "STOP" else 0.0,
        "year": erow["date"][:4],
        "regime_adx": round(erow.get("adx") or 0, 1),
        "regime_atr_pct_rank": round(erow.get("atr_pct_rank") or 0, 3),
        "eff_ratio": round(erow.get("eff_ratio") or 0, 3),
    })
