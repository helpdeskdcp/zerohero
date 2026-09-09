"""
Shared, causal per-bar feature frame for all three engines. value[i] depends
only on bars <= i. Reuses the validated orderflow feature layer for structure /
regime / value-area / reclaim, and adds the extras Engine 1 & 2 need.
"""
from __future__ import annotations

from ..orderflow import indicators as I
from ..orderflow import orderflow as OF


def _of_cfg(cfg: dict) -> dict:
    e1 = cfg["e1"]
    return {
        "eps": 1e-9, "warmup_bars": cfg["warmup_bars"],
        "ema_fast": e1["ema_fast"], "ema_slow": e1["ema_slow"], "ema_trend": e1["ema_trend"],
        "atr_period": 14, "adx_period": 14, "rsi_period": 14,
        "pivot_halfwidth": 3, "vwap_method": "PRICE_PROXY_EQUAL_WEIGHT",
        "of_lookback": 10, "of_divergence_pivots": 2,
        "adx_trend_min": 22.0, "adx_range_max": 18.0,
        "atr_pct_hi_pctl": 0.70, "atr_pct_lo_pctl": 0.30,
        "eff_ratio_lookback": cfg["e1"]["eff_ratio_lookback"],
    }


def _rma(x, n):
    out = [None] * len(x)
    a = 1.0 / n
    r, acc, cnt = None, 0.0, 0
    for i, v in enumerate(x):
        if v is None:
            out[i] = r
            continue
        if r is None:
            acc += v
            cnt += 1
            if cnt == n:
                r = acc / n
                out[i] = r
        else:
            r = a * v + (1 - a) * r
            out[i] = r
    return out


def _dmi(h, l, c, n):
    """Wilder +DI / -DI (orderflow.indicators only exposes ADX, not the DIs)."""
    up = [None] + [h[i] - h[i - 1] for i in range(1, len(h))]
    dn = [None] + [l[i - 1] - l[i] for i in range(1, len(l))]
    pdm = [None if u is None else (u if (u > d and u > 0) else 0.0) for u, d in zip(up, dn)]
    mdm = [None if d is None else (d if (d > u and d > 0) else 0.0) for u, d in zip(up, dn)]
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
                          for i in range(1, len(h))]
    trur = _rma(tr, n)
    rp, rm = _rma(pdm, n), _rma(mdm, n)
    di_p = [None if (a is None or not t) else 100.0 * a / t for a, t in zip(rp, trur)]
    di_m = [None if (b is None or not t) else 100.0 * b / t for b, t in zip(rm, trur)]
    return di_p, di_m


def _donchian(h, l, n):
    hi = [None] * len(h)
    lo = [None] * len(l)
    for i in range(len(h)):
        a = max(0, i - n + 1)
        # exclude the current bar so a close >= prior-N high is a genuine breakout
        seg_h = h[a:i] or h[a:i + 1]
        seg_l = l[a:i] or l[a:i + 1]
        hi[i] = max(seg_h)
        lo[i] = min(seg_l)
    return hi, lo


def build_frame(bars: list[dict], cfg: dict) -> list[dict]:
    n = len(bars)
    of = OF.build(bars, _of_cfg(cfg))
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    c = [b["c"] for b in bars]
    o = [b["o"] for b in bars]

    e1 = cfg["e1"]
    don_hi, don_lo = _donchian(h, l, e1["donchian_n"])
    di_p, di_m = _dmi(h, l, c, 14)
    ema_t = I.ema(c, e1["ema_trend"])
    slb = e1["ema_trend_slope_lookback"]
    hma = I.ema(c, cfg["e2"]["hma_len"])   # HMA approx via EMA of same length (indicators has no hma here)
    atr_pct_hist: list[float] = []

    out = []
    for i in range(n):
        r = dict(of[i])                       # structure_state, regime_*, vwap, atr, adx, rsi,
                                              # ema_fast/slow/trend, value_area, reclaim, diag, ...
        atr = r["atr"] or 0.0
        rng = max(1e-9, h[i] - l[i])
        body = abs(c[i] - o[i])
        r["body_frac"] = body / rng
        r["close_loc"] = (c[i] - l[i]) / rng
        r["range_atr"] = (rng / atr) if atr > 0 else 0.0
        r["don_hi"] = don_hi[i]
        r["don_lo"] = don_lo[i]
        r["don_mid"] = (don_hi[i] + don_lo[i]) / 2.0 if (don_hi[i] is not None and don_lo[i] is not None) else None
        r["ema_trend_slope"] = ((ema_t[i] - ema_t[i - slb]) / slb) if i >= slb else 0.0
        r["hma"] = hma[i]
        r["hma_up"] = (i > 0 and hma[i] is not None and hma[i - 1] is not None and hma[i] > hma[i - 1])
        r["hma_dn"] = (i > 0 and hma[i] is not None and hma[i - 1] is not None and hma[i] < hma[i - 1])
        r["di_plus"] = di_p[i]
        r["di_minus"] = di_m[i]

        ap = (atr / c[i]) if c[i] else 0.0
        atr_pct_hist.append(ap)
        srt = sorted(atr_pct_hist[-750:])
        r["atr_pct"] = ap
        r["atr_pct_rank"] = (srt.index(ap) / max(1, len(srt) - 1)) if len(srt) > 1 else 0.5

        # nearest validated S/R = closest of {last confirmed pivots, value-area edges}
        cands = []
        for piv in (r.get("last_hi_piv"), r.get("last_lo_piv")):
            if piv:
                cands.append(piv[0])
        va = r.get("value_area")
        if va:
            cands += [va["vah"], va["val"], va["poc"]]
        if cands and atr > 0:
            nearest = min(cands, key=lambda p: abs(p - c[i]))
            r["sr_level"] = round(nearest, 2)
            r["sr_dist_atr"] = abs(nearest - c[i]) / atr
        else:
            r["sr_level"] = None
            r["sr_dist_atr"] = None

        out.append(r)
    return out
