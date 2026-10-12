"""Trade simulation + statistics for Smart-BoS signals (research only).

Execution model (deliberately conservative, no look-ahead):
* signal bar = confirmed 1m bar; fill = OPEN of the next bar.
* planned SL/targets are fixed at signal time (Pine levels) or derived from the
  entry price and the day's Renko brick (renko levels).
* inside a bar the stop is evaluated BEFORE targets (adverse-first); a gap through a
  level fills at the open (worse), never at the level.
* a signal whose fill already sits beyond its stop is skipped (counted).
* one position at a time; signals arriving while in a trade are skipped (counted).
* flat at `eod_minute` (close of the last allowed bar) -- intraday only.
* costs are round-trip basis points of the entry price; returns are reported in R
  (price move / planned risk) and bps so that 2017 and 2026 index levels compare.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from .data import ist_minutes


@dataclass
class Trade:
    sig_i: int
    entry_i: int
    exit_i: int
    side: int
    entry: float
    exit: float
    risk: float
    pnl: float
    r: float
    bps: float
    mae: float
    mfe: float
    reason: str
    day: int
    kind: str
    parts: tuple


def _levels(sig, entry, mode, renko_cfg):
    side = sig.side
    if mode == "pine":
        return sig.sl, list(sig.targets), abs(sig.close - sig.sl)
    B = sig.brick
    if B <= 0:
        return None
    sl = entry - side * renko_cfg["sl_bricks"] * B
    step = renko_cfg["step_bricks"]
    return sl, [entry + side * step * (j + 1) * B for j in range(4)], renko_cfg["sl_bricks"] * B


def simulate(bars: dict, signals: list, *, mode: str = "pine", exit_model: str = "T2", renko_cfg: dict | None = None,
             require_renko: dict | None = None, eod_minute: int = 925, be_after_t1: bool = False,
             side_filter: int = 0) -> tuple[list[Trade], dict]:
    """exit_model: T1..T4 (all-out at that target) | SCALE (equal parts at each of T1..T3, T4 if renko).
    require_renko: {"min_run": N} keeps only signals whose Renko direction == side and run >= N."""
    t, o, h, l, c = bars["t"], bars["o"], bars["h"], bars["l"], bars["c"]
    days, mod = ist_minutes(t)
    n = len(t)
    renko_cfg = renko_cfg or {"sl_bricks": 2, "step_bricks": 1}
    trades, skipped = [], {"in_trade": 0, "gap_beyond_stop": 0, "renko_filter": 0, "no_brick": 0, "side": 0}
    busy_until = -1
    for s in signals:
        if side_filter and s.side != side_filter:
            skipped["side"] += 1
            continue
        if s.i <= busy_until:
            skipped["in_trade"] += 1
            continue
        if require_renko and not (s.renko_dir == s.side and s.renko_run >= require_renko["min_run"]):
            skipped["renko_filter"] += 1
            continue
        e = s.i + 1
        if e >= n or days[e] != days[s.i] or mod[e] >= eod_minute:
            continue
        entry = o[e]
        lv = _levels(s, entry, mode, renko_cfg)
        if lv is None:
            skipped["no_brick"] += 1
            continue
        sl, tg, risk = lv
        side = s.side
        if (side == 1 and entry <= sl) or (side == -1 and entry >= sl) or risk <= 0:
            skipped["gap_beyond_stop"] += 1
            continue
        if exit_model == "SCALE":
            use = tg[:3] if mode == "pine" else tg[:4]
            fr = [1.0 / len(use)] * len(use)
        else:
            k = int(exit_model[1]) - 1
            if k >= len(tg):
                raise ValueError(f"{exit_model} not available for mode={mode}")
            use, fr = [tg[k]], [1.0]
        remain, cur_sl, parts, mae, mfe, j = 1.0, sl, [], 0.0, 0.0, e
        reason, exit_i = "EOD", e
        nxt = 0
        while j < n and days[j] == days[s.i]:
            last_bar = mod[j] >= eod_minute - 1 or (j + 1 >= n or days[j + 1] != days[s.i])
            lo, hi = l[j], h[j]
            mae = min(mae, side * ((lo if side == 1 else hi) - entry))
            mfe = max(mfe, side * ((hi if side == 1 else lo) - entry))
            stop_hit = (lo <= cur_sl) if side == 1 else (hi >= cur_sl)
            if stop_hit:
                px = min(o[j], cur_sl) if side == 1 else max(o[j], cur_sl)
                if j == e:
                    px = cur_sl
                parts.append((remain, px))
                remain, reason, exit_i = 0.0, ("BE" if cur_sl == entry else "SL"), j
                break
            while nxt < len(use) and ((hi >= use[nxt]) if side == 1 else (lo <= use[nxt])):
                px = max(o[j], use[nxt]) if side == 1 else min(o[j], use[nxt])
                if j == e:
                    px = use[nxt]
                take = fr[nxt] if exit_model == "SCALE" else remain
                parts.append((min(take, remain), px))
                remain -= min(take, remain)
                if nxt == 0 and be_after_t1:
                    cur_sl = entry
                nxt += 1
                exit_i, reason = j, f"T{nxt}"
                if remain <= 1e-9:
                    break
            if remain <= 1e-9:
                break
            if last_bar:
                parts.append((remain, c[j]))
                remain, reason, exit_i = 0.0, "EOD", j
                break
            j += 1
        if remain > 1e-9:                                   # data ended inside the day
            parts.append((remain, c[min(j, n - 1)]))
            exit_i = min(j, n - 1)
        pnl = sum(w * side * (px - entry) for w, px in parts)
        trades.append(Trade(s.i, e, exit_i, side, entry, parts[-1][1], risk, pnl, pnl / risk,
                            1e4 * pnl / entry, mae, mfe, reason, int(days[e]), s.kind, tuple(parts)))
        busy_until = exit_i
    return trades, skipped


def summarize(trades: list[Trade], *, cost_bps: float = 0.0, boot: int = 2000, seed: int = 7) -> dict:
    if not trades:
        return {"n": 0}
    r = np.array([t.r - cost_bps * t.entry / 1e4 / t.risk for t in trades])
    b = np.array([t.bps - cost_bps for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    eq = np.cumsum(r)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    rnd = random.Random(seed)
    means = []
    n = len(r)
    rl = r.tolist()
    for _ in range(boot):
        means.append(sum(rnd.choice(rl) for _ in range(n)) / n)
    means.sort()
    pf = float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else math.inf
    return {"n": n, "win": float((r > 0).mean()), "mean_r": float(r.mean()), "mean_bps": float(b.mean()),
            "pf": pf, "total_r": float(r.sum()), "max_dd_r": dd, "ci95_r": (means[int(.025 * boot)], means[int(.975 * boot) - 1]),
            "avg_win_r": float(wins.mean()) if len(wins) else 0.0, "avg_loss_r": float(losses.mean()) if len(losses) else 0.0,
            "mean_mae_r": float(np.mean([t.mae / t.risk for t in trades])), "mean_mfe_r": float(np.mean([t.mfe / t.risk for t in trades])),
            "avg_hold_bars": float(np.mean([t.exit_i - t.entry_i for t in trades]))}


def by_year(trades: list[Trade], bars: dict, cost_bps: float = 0.0) -> dict:
    import datetime as dt
    out = {}
    for t in trades:
        y = dt.date.fromordinal(719163 + t.day).year
        out.setdefault(y, []).append(t)
    return {y: summarize(v, cost_bps=cost_bps, boot=200) for y, v in sorted(out.items())}
