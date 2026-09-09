"""
Order-flow FEATURE layer (spec L2-L4), NIFTY cash index = PROXY forms only.

Per bar it produces:
  flow_proxy        candle pressure net in [-1, 1]  (method="PRESSURE_PROXY")
  cvd_proxy         session-cumulative flow_proxy   (NOT true CVD)
  cvd_slope         OLS slope of cvd_proxy over of_lookback
  flow_vs_price     proxy divergence tag on pivots  (-1 bearish / 0 / +1 bullish)
  structure_state   HH/HL/LH/LL/BOS/CHoCH/RANGE + trend sign
  regime            (trend, vol) pair + a numeric regime_fit basis
  vwap_dev_atr      (close - vwap_proxy) / atr
  value_area        {poc, vah, val} from the session TPO profile (exact)
  reclaim           structural sweep/exhaustion proxy: pierce of a level then
                    reclaim within FWD bars, distance ratio (Stage-7 knee ~0.60)
Nothing here decides a trade. Nothing is labelled delta / CVD / footprint.
"""
from __future__ import annotations

from . import indicators as I
from .config import merged


def _pressure(bar: dict) -> float:
    """Candle pressure proxy in [-1, 1]. body direction + wick defence +
    close location. Zero-range -> 0. This is NOT trade delta."""
    o, h, l, c = bar["o"], bar["h"], bar["l"], bar["c"]
    rng = h - l
    if rng <= 1e-9:
        return 0.0
    body = (c - o) / rng                       # signed, [-1, 1]
    lower_wick = (min(o, c) - l) / rng         # buyers defended
    upper_wick = (h - max(o, c)) / rng         # sellers defended
    close_loc = (c - l) / rng * 2.0 - 1.0      # -1 at low, +1 at high
    net = 0.55 * body + 0.20 * close_loc + 0.25 * (lower_wick - upper_wick)
    return max(-1.0, min(1.0, net))


def _ols_slope(ys: list[float]) -> float:
    n = len(ys)
    if n < 2:
        return 0.0
    xm = (n - 1) / 2.0
    ym = sum(ys) / n
    num = sum((i - xm) * (y - ym) for i, y in enumerate(ys))
    den = sum((i - xm) ** 2 for i in range(n))
    return num / den if den else 0.0


def _tpo_profile(bars: list[dict], lo_i: int, hi_i: int, tick: float, value_pct: float = 0.70):
    """Exact TPO (time-price-opportunity) profile over bars[lo_i:hi_i]: count how
    many bars' [low, high] touched each price bin. POC / value area standard."""
    if hi_i - lo_i < 3:
        return None
    lo = min(b["l"] for b in bars[lo_i:hi_i])
    hi = max(b["h"] for b in bars[lo_i:hi_i])
    if hi - lo <= tick:
        return None
    nb = int((hi - lo) / tick) + 1
    counts = [0] * nb
    for b in bars[lo_i:hi_i]:
        a = max(0, int((b["l"] - lo) / tick))
        z = min(nb - 1, int((b["h"] - lo) / tick))
        for k in range(a, z + 1):
            counts[k] += 1
    poc_k = max(range(nb), key=lambda k: counts[k])
    total = sum(counts)
    inside = counts[poc_k]
    a = z = poc_k
    while inside < value_pct * total and (a > 0 or z < nb - 1):
        up_c = counts[z + 1] if z < nb - 1 else -1
        dn_c = counts[a - 1] if a > 0 else -1
        if up_c >= dn_c:
            z += 1
            inside += max(0, up_c)
        else:
            a -= 1
            inside += max(0, dn_c)
    return {"poc": lo + (poc_k + 0.5) * tick,
            "val": lo + a * tick,
            "vah": lo + (z + 1) * tick}


def build(bars: list[dict], cfg: dict | None = None) -> list[dict]:
    c = cfg or merged()
    n = len(bars)
    o = [b["o"] for b in bars]
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    cl = [b["c"] for b in bars]
    hlc3 = [b["hlc3"] for b in bars]
    sess = [b["session_date"] for b in bars]

    ema_f = I.ema(cl, c["ema_fast"])
    ema_s = I.ema(cl, c["ema_slow"])
    ema_t = I.ema(cl, c["ema_trend"])
    atr = I.atr(h, l, cl, c["atr_period"])
    adx = I.adx(h, l, cl, c["adx_period"])
    rsi = I.rsi(cl, c["rsi_period"])
    vwap = I.session_vwap_proxy(hlc3, sess)
    er = I.efficiency_ratio(cl, c["eff_ratio_lookback"])
    hi_piv, lo_piv = I.pivots(h, l, c["pivot_halfwidth"])
    w = c["pivot_halfwidth"]
    # a pivot at index p is only *confirmed* at p + w -> map "known at bar i"
    hi_known = {p + w: p for p in hi_piv}
    lo_known = {p + w: p for p in lo_piv}

    pr = [_pressure(b) for b in bars]
    cvd = []
    cur = None
    acc = 0.0
    for i in range(n):
        if sess[i] != cur:
            cur, acc = sess[i], 0.0
        acc += pr[i]
        cvd.append(acc)

    # ATR% percentile reference (trailing, causal)
    atr_pct = [(atr[i] / cl[i]) if (atr[i] and cl[i]) else None for i in range(n)]

    # session-start index for TPO window
    sess_start = {}
    for i in range(n):
        sess_start.setdefault(sess[i], i)

    out = []
    last_hi_piv = last_lo_piv = None          # (price, idx) confirmed so far
    prev_hi_piv = prev_lo_piv = None
    trend = 0                                  # +1 up, -1 down, 0 range
    last_bos_dir = 0
    atr_hist: list[float] = []

    tick = {"NIFTY": 2.0}.get(bars and "NIFTY" or "", 2.0)

    for i in range(n):
        b = bars[i]
        # roll confirmed pivots
        if i in hi_known:
            p = hi_known[i]
            prev_hi_piv, last_hi_piv = last_hi_piv, (h[p], p)
        if i in lo_known:
            p = lo_known[i]
            prev_lo_piv, last_lo_piv = last_lo_piv, (l[p], p)

        # structure state
        st = "RANGE"
        if last_hi_piv and prev_hi_piv and last_lo_piv and prev_lo_piv:
            hh = last_hi_piv[0] > prev_hi_piv[0]
            hl = last_lo_piv[0] > prev_lo_piv[0]
            lh = last_hi_piv[0] < prev_hi_piv[0]
            ll = last_lo_piv[0] < prev_lo_piv[0]
            if hh and hl:
                trend, st = 1, "HH_HL"
            elif lh and ll:
                trend, st = -1, "LH_LL"
            else:
                st = "MIXED"
        # BOS / CHoCH on close through the last opposing pivot
        if last_hi_piv and cl[i] > last_hi_piv[0]:
            st = "BOS_UP" if last_bos_dir >= 0 else "CHOCH_UP"
            last_bos_dir = 1
        elif last_lo_piv and cl[i] < last_lo_piv[0]:
            st = "BOS_DN" if last_bos_dir <= 0 else "CHOCH_DN"
            last_bos_dir = -1

        # proxy divergence on the two most recent confirmed same-type pivots
        fvp = 0
        if prev_lo_piv and last_lo_piv:
            if last_lo_piv[0] < prev_lo_piv[0] and cvd[last_lo_piv[1]] > cvd[prev_lo_piv[1]]:
                fvp = 1                                     # bullish: lower low, higher flow low
        if prev_hi_piv and last_hi_piv:
            if last_hi_piv[0] > prev_hi_piv[0] and cvd[last_hi_piv[1]] < cvd[prev_hi_piv[1]]:
                fvp = -1                                    # bearish

        # regime
        a = atr_pct[i]
        if a is not None:
            atr_hist.append(a)
        srt = sorted(atr_hist[-500:])
        vol = "MID"
        if srt:
            if a is not None and a >= I.pctl(srt, c["atr_pct_hi_pctl"]):
                vol = "HIGH"
            elif a is not None and a <= I.pctl(srt, c["atr_pct_lo_pctl"]):
                vol = "LOW"
        adx_i = adx[i] or 0.0
        eff = er[i] or 0.0
        if adx_i >= c["adx_trend_min"] and eff >= 0.35:
            tstate = "TRENDING"
        elif adx_i <= c["adx_range_max"] or eff <= 0.15:
            tstate = "RANGING" if vol != "HIGH" else "CHOP"
        else:
            tstate = "MIXED"

        # value area (session TPO so far, causal)
        va = _tpo_profile(bars, sess_start[sess[i]], i + 1, tick) if i - sess_start[sess[i]] >= 3 else None

        # reclaim proxy: was a recent pivot pierced then reclaimed within pivot_halfwidth*2 bars?
        reclaim = {"fired": False, "dir": 0, "dist_ratio": None}
        lvl = None
        if last_lo_piv and last_lo_piv[1] <= i - 1:
            lvl = last_lo_piv
            pierce = min(l[max(0, i - 4):i + 1])
            if pierce < lvl[0] and cl[i] > lvl[0]:
                rng = max(1e-9, h[i] - pierce)
                reclaim = {"fired": True, "dir": 1,
                           "dist_ratio": round((lvl[0] - pierce) / rng, 3)}
        if not reclaim["fired"] and last_hi_piv and last_hi_piv[1] <= i - 1:
            lvl = last_hi_piv
            pierce = max(h[max(0, i - 4):i + 1])
            if pierce > lvl[0] and cl[i] < lvl[0]:
                rng = max(1e-9, pierce - l[i])
                reclaim = {"fired": True, "dir": -1,
                           "dist_ratio": round((pierce - lvl[0]) / rng, 3)}

        cvd_slope = _ols_slope(cvd[max(0, i - c["of_lookback"] + 1):i + 1])

        out.append({
            "i": i, "t": b["t"], "session_date": sess[i], "hhmm": b["hhmm"],
            "minute_of_day": b["minute_of_day"],
            "open": o[i], "high": h[i], "low": l[i], "close": cl[i],
            "atr": atr[i], "adx": adx_i, "rsi": rsi[i], "ema_fast": ema_f[i],
            "ema_slow": ema_s[i], "ema_trend": ema_t[i], "vwap": vwap[i],
            "vwap_dev_atr": ((cl[i] - vwap[i]) / atr[i]) if (atr[i] and vwap[i] is not None) else None,
            "flow_proxy": pr[i], "cvd_proxy": cvd[i], "cvd_slope": cvd_slope,
            "flow_vs_price": fvp,
            "structure_state": st, "trend": trend,
            "regime_trend": tstate, "regime_vol": vol, "eff_ratio": eff,
            "value_area": va, "reclaim": reclaim,
            "last_hi_piv": last_hi_piv, "last_lo_piv": last_lo_piv,
            "method": {"flow": "PRESSURE_PROXY", "cvd": "CUM_PRESSURE_PROXY",
                       "vwap": c["vwap_method"], "profile": "TPO_EXACT"},
        })
    return out
