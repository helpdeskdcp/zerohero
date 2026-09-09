"""
E1 TREND signal on DAILY bars -- the exact frozen intraday tri_compare E1 logic:

  trend state = close > EMA_slow AND EMA_fast > EMA_slow AND EMA200 slope > 0   (mirror for short)
  trigger     = Donchian(N) breakout in the trend direction
  gates       = ATR% inside a volatility band  +  trend persistence  +  Kaufman
                efficiency ratio  +  ADX

`build()` returns a per-bar frame; `target_direction()` gives the desired
position for each bar (+1 long / -1 short / 0 flat). The harness turns that into
next-open entries, an ATR stop, a trailing exit and a trend-flip exit.
"""
from __future__ import annotations

from . import indicators as I


def build(bars: list[dict], cfg: dict) -> list[dict]:
    n = len(bars)
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    c = [b["c"] for b in bars]
    o = [b["o"] for b in bars]

    ema_f = I.ema(c, cfg["ema_fast"])
    ema_s = I.ema(c, cfg["ema_slow"])
    ema_t = I.ema(c, cfg["ema_trend"])
    ema_t_slope = I.slope(ema_t, cfg["ema_trend_slope_lookback"])
    at = I.atr(h, l, c, cfg["atr_period"])
    ad = I.adx(h, l, c, cfg["adx_period"])
    er = I.efficiency_ratio(c, cfg["eff_ratio_lookback"])
    don_hi, don_lo = I.donchian(h, l, cfg["donchian_n"])

    atr_hist: list[float] = []
    out = []
    persist = 0
    last_state = 0
    for i in range(n):
        st = 0
        if None not in (ema_f[i], ema_s[i]):
            if c[i] > ema_s[i] and ema_f[i] > ema_s[i] and ema_t_slope[i] > 0:
                st = 1
            elif c[i] < ema_s[i] and ema_f[i] < ema_s[i] and ema_t_slope[i] < 0:
                st = -1
        persist = persist + 1 if (st != 0 and st == last_state) else (1 if st != 0 else 0)
        last_state = st

        ap = (at[i] / c[i]) if (at[i] and c[i]) else 0.0
        atr_hist.append(ap)
        srt = sorted(atr_hist[-1500:])
        apr = (srt.index(ap) / max(1, len(srt) - 1)) if len(srt) > 1 else 0.5

        out.append({
            "i": i, "date": bars[i]["date"], "o": o[i], "h": h[i], "l": l[i], "c": c[i],
            "atr": at[i], "adx": ad[i], "eff_ratio": er[i],
            "ema_fast": ema_f[i], "ema_slow": ema_s[i], "ema_trend": ema_t[i],
            "ema_trend_slope": ema_t_slope[i], "trend_state": st, "persist": persist,
            "atr_pct": ap, "atr_pct_rank": apr,
            "don_hi": don_hi[i], "don_lo": don_lo[i],
        })
    return out


def target_direction(frame: list[dict], cfg: dict) -> list[int]:
    """+1 / -1 / 0 desired position per bar. A bar is a fresh SETUP only when the
    Donchian breakout occurs; between breakouts the direction just carries the
    prior setup's sign (position management is the harness's job)."""
    warm = cfg["warmup_days"]
    tgt = [0] * len(frame)
    live = 0
    for i, r in enumerate(frame):
        if i < warm:
            continue
        st = r["trend_state"]
        apr = r["atr_pct_rank"]
        atr = r["atr"] or 0.0
        gates_ok = (
            st != 0
            and cfg["atr_pct_lo_pctl"] <= apr <= cfg["atr_pct_hi_pctl"]
            and r["persist"] >= cfg["min_trend_persist_days"]
            and (r["eff_ratio"] or 0.0) >= cfg["eff_ratio_min"]
            and (r["adx"] or 0.0) >= cfg["adx_min"]
        )
        buf = cfg["donchian_buffer_atr"] * atr
        setup = 0
        if gates_ok and st == 1 and r["don_hi"] is not None and r["c"] >= r["don_hi"] + buf:
            setup = 1
        elif gates_ok and st == -1 and r["don_lo"] is not None and r["c"] <= r["don_lo"] - buf:
            setup = -1

        if setup != 0:
            live = setup
        elif st == 0 or (live == 1 and st == -1) or (live == -1 and st == 1):
            live = 0                    # trend state gone / flipped -> stand down
        tgt[i] = live
    return tgt
