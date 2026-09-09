"""
ENGINE 1 -- CLAUDE TREND ENGINE  (time-series momentum / trend-following).

Direction from EMA stack + EMA200 slope. Trigger on a Donchian(N) channel
breakout. Gated by an ATR volatility-regime band and an anti-whipsaw stack
(trend persistence + Kaufman efficiency ratio + ADX). Entry = the breakout
bar's close (the harness adds slippage and the shared ATR-SL / trailing exit).
"""
from __future__ import annotations


def _trend_state(r: dict) -> int:
    """+1 up / -1 down / 0 none, from the EMA stack + EMA200 slope."""
    ef, es, c = r.get("ema_fast"), r.get("ema_slow"), r.get("close")
    sl = r.get("ema_trend_slope") or 0.0
    if None in (ef, es, c):
        return 0
    if c > es and ef > es and sl > 0:
        return 1
    if c < es and ef < es and sl < 0:
        return -1
    return 0


def signals(frame: list[dict], cfg: dict) -> list[dict]:
    e = cfg["e1"]
    warm = cfg["warmup_bars"]
    out: list[dict] = []
    persist = 0
    last_state = 0
    prev_dir = None
    last_fire_i = -10_000
    for i, r in enumerate(frame):
        st = _trend_state(r)
        persist = persist + 1 if (st != 0 and st == last_state) else (1 if st != 0 else 0)
        last_state = st
        if i < warm:
            continue

        # volatility-regime filter
        apr = r.get("atr_pct_rank")
        if apr is None or not (e["atr_pct_lo_pctl"] <= apr <= e["atr_pct_hi_pctl"]):
            continue
        # anti-whipsaw
        if persist < e["min_trend_persist_bars"]:
            continue
        if (r.get("eff_ratio") or 0.0) < e["eff_ratio_min"]:
            continue
        if (r.get("adx") or 0.0) < e["adx_min"]:
            continue

        atr = r.get("atr") or 0.0
        buf = e["donchian_buffer_atr"] * atr
        direction = None
        if st == 1 and r.get("don_hi") is not None and r["close"] >= r["don_hi"] + buf:
            direction = "LONG"
        elif st == -1 and r.get("don_lo") is not None and r["close"] <= r["don_lo"] - buf:
            direction = "SHORT"
        if direction is None:
            continue

        if direction == prev_dir and (i - last_fire_i) < cfg["cooldown_bars"]:
            continue
        prev_dir, last_fire_i = direction, i
        out.append({
            "engine": "E1_TREND", "signal_index": i, "fill_index": i,
            "direction": direction, "entry_ref": r["close"],
            "rk_score": round(min(100.0, 40.0 + (r.get("eff_ratio") or 0) * 60.0), 1),
            "kind": "donchian_breakout",
            "context": {"trend_state": st, "persist": persist,
                        "eff_ratio": round(r.get("eff_ratio") or 0, 3),
                        "adx": round(r.get("adx") or 0, 1), "atr_pct_rank": round(apr, 3)},
        })
    return out
