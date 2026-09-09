"""Faithful port of the SSL Hybrid PRO signal + risk engine. Causal & deterministic.
`build_signals` returns a per-bar frame; `find_setups` turns fresh BUY/SELL
signals into entry / SL / T1-T3 setups. Nothing is fitted from data."""
from __future__ import annotations

from . import indicators as I
from .config import merged


def build_signals(bars: list[dict], cfg: dict) -> list[dict]:
    n = len(bars)
    o = [b["o"] for b in bars]
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    c = [b["c"] for b in bars]
    v = [b["v"] for b in bars]
    hlc3 = [b["hlc3"] for b in bars]
    sess = [b["session_date"] for b in bars]
    use_vol = bool(bars and bars[0].get("has_volume"))

    ssl1_hi, ssl1_lo = I.ema(h, cfg["ssl1_len"]), I.ema(l, cfg["ssl1_len"])
    ssl2_hi, ssl2_lo = I.ema(h, cfg["ssl2_len"]), I.ema(l, cfg["ssl2_len"])
    exit_hi, exit_lo = I.ema(h, cfg["exit_len"]), I.ema(l, cfg["exit_len"])  # parity w/ Pine (unused by signal)
    baseline = I.ema(c, cfg["baseline_len"])
    ema200 = I.ema(c, cfg["ema_len"])
    hma = I.hma(c, cfg["hma_len"])
    rsi = I.rsi(c, cfg["rsi_len"])
    di_p, di_m, adx = I.dmi(h, l, c, cfg["adx_len"], cfg["adx_len"])
    atr = I.atr(h, l, c, cfg["atr_len"])
    vwap = I.session_vwap(hlc3, v, sess, anchored=cfg["session_anchored_vwap"], use_volume=use_vol)
    avg_vol = I.sma(v, cfg["vol_len"])

    # SSL states (Pine: var int state; close>hi ->1, close<lo ->-1, else carry)
    def _state(hi, lo):
        st = 0
        out = []
        for i in range(n):
            if hi[i] is not None and c[i] > hi[i]:
                st = 1
            elif lo[i] is not None and c[i] < lo[i]:
                st = -1
            out.append(st)
        return out

    s1, s2 = _state(ssl1_hi, ssl1_lo), _state(ssl2_hi, ssl2_lo)
    ssl1_up = [(ssl1_lo[i] if s1[i] < 0 else ssl1_hi[i]) for i in range(n)]
    ssl1_dn = [(ssl1_hi[i] if s1[i] < 0 else ssl1_lo[i]) for i in range(n)]
    ssl2_up = [(ssl2_lo[i] if s2[i] < 0 else ssl2_hi[i]) for i in range(n)]
    ssl2_dn = [(ssl2_hi[i] if s2[i] < 0 else ssl2_lo[i]) for i in range(n)]
    x_bull = I.crossover(ssl1_up, ssl1_dn)
    x_bear = I.crossunder(ssl1_up, ssl1_dn)

    out = []
    prev_strong_bull = prev_strong_bear = False
    for i in range(n):
        b = bars[i]
        rng = h[i] - l[i]
        body = abs(c[i] - o[i])
        body_pct = (body / rng * 100.0) if rng > 0 else 0.0
        bull_candle = c[i] > o[i] and body_pct >= 50.0
        bear_candle = c[i] < o[i] and body_pct >= 50.0

        ssl_bull = (ssl1_up[i] is not None and ssl2_up[i] is not None
                    and ssl1_up[i] > ssl1_dn[i] and ssl2_up[i] > ssl2_dn[i])
        ssl_bear = (ssl1_up[i] is not None and ssl2_up[i] is not None
                    and ssl1_up[i] < ssl1_dn[i] and ssl2_up[i] < ssl2_dn[i])

        above_base = baseline[i] is not None and c[i] > baseline[i]
        below_base = baseline[i] is not None and c[i] < baseline[i]
        above_ema = ema200[i] is not None and c[i] > ema200[i]
        below_ema = ema200[i] is not None and c[i] < ema200[i]
        above_vwap = vwap[i] is not None and c[i] > vwap[i]
        below_vwap = vwap[i] is not None and c[i] < vwap[i]
        hma_bull = hma[i] is not None and hma[i - 1] is not None and hma[i] > hma[i - 1]
        hma_bear = hma[i] is not None and hma[i - 1] is not None and hma[i] < hma[i - 1]
        rsi_bull = rsi[i] is not None and rsi[i] > 55.0
        rsi_bear = rsi[i] is not None and rsi[i] < 45.0
        adx_bull = adx[i] is not None and di_p[i] is not None and adx[i] >= cfg["adx_min"] and di_p[i] > di_m[i]
        adx_bear = adx[i] is not None and di_m[i] is not None and adx[i] >= cfg["adx_min"] and di_m[i] > di_p[i]
        vol_ok = avg_vol[i] is not None and v[i] >= avg_vol[i] * cfg["vol_mult"]

        bull_raw = (20 * ssl_bull + 10 * x_bull[i] + 10 * above_base + 10 * above_ema
                    + 10 * above_vwap + 10 * hma_bull + 10 * rsi_bull + 10 * adx_bull
                    + 5 * (vol_ok and c[i] > o[i]) + 5 * bull_candle)
        bear_raw = (20 * ssl_bear + 10 * x_bear[i] + 10 * below_base + 10 * below_ema
                    + 10 * below_vwap + 10 * hma_bear + 10 * rsi_bear + 10 * adx_bear
                    + 5 * (vol_ok and c[i] < o[i]) + 5 * bear_candle)
        bull_score = round(bull_raw * 100.0 / 110.0)
        bear_score = round(bear_raw * 100.0 / 110.0)

        strong_bull = (bull_score >= cfg["min_confidence"] and ssl_bull and above_base
                       and above_vwap and adx_bull)
        strong_bear = (bear_score >= cfg["min_confidence"] and ssl_bear and below_base
                       and below_vwap and adx_bear)
        buy_sig = strong_bull and not prev_strong_bull
        sell_sig = strong_bear and not prev_strong_bear
        prev_strong_bull, prev_strong_bear = strong_bull, strong_bear

        out.append({
            "i": i, "t": b["t"], "session_date": b["session_date"], "hhmm": b["hhmm"],
            "minute_of_day": b["minute_of_day"], "close": c[i], "atr": atr[i],
            "bull_score": bull_score, "bear_score": bear_score,
            "strong_bull": strong_bull, "strong_bear": strong_bear,
            "buy_signal": buy_sig, "sell_signal": sell_sig,
            # --- read-only diagnostics (Stage-2 filter screening; NOT used by the
            #     signal, which is fully defined above) ---
            "diag": {
                "adx": adx[i], "di_plus": di_p[i], "di_minus": di_m[i], "rsi": rsi[i],
                "ema200": ema200[i], "hma": hma[i], "hma_prev": hma[i - 1] if i else None,
                "vwap": vwap[i], "baseline": baseline[i],
                "above_ema": above_ema, "below_ema": below_ema,
                "hma_bull": hma_bull, "hma_bear": hma_bear,
                "rsi_bull": rsi_bull, "rsi_bear": rsi_bear,
                "x_bull": x_bull[i], "x_bear": x_bear[i],
            },
        })
    return out


def _passes_filters(row: dict, side: str, c: dict) -> bool:
    """Stage-2 confirmation filters, chosen from IN-SAMPLE screening and OOS/
    walk-forward validated. Each is opt-in via config; all OFF -> baseline.

      filter_session_window  [start_min, end_min] IST -> keep only entries whose
                             minute-of-day is in [start, end). Screening: the
                             opening hour and the last ~90 min bleed; 10:00-13:30
                             wins ~+9pp. Recommended [600, 810].
      filter_require_hma_slope  True -> HMA must slope with the trade side.
      filter_max_vwap_ext_atr   float -> drop entries > this many ATR from VWAP
                             (>3 ATR overextension had ~19% win in-sample).
      filter_max_adx            float -> drop entries with ADX above this
                             ("don't chase an already-stretched trend").
    """
    win = c.get("filter_session_window")
    if win:
        m = row["minute_of_day"]
        if not (win[0] <= m < win[1]):
            return False
    d = row.get("diag") or {}
    if c.get("filter_require_hma_slope"):
        if not (d.get("hma_bull") if side == "LONG" else d.get("hma_bear")):
            return False
    cap = c.get("filter_max_vwap_ext_atr")
    if cap is not None:
        atr = row["atr"] or 1e-9
        vwap = d.get("vwap")
        if vwap is not None and abs(row["close"] - vwap) / atr > cap:
            return False
    adx_cap = c.get("filter_max_adx")
    if adx_cap is not None and (d.get("adx") or 0.0) > adx_cap:
        return False
    return True


def find_setups(bars: list[dict], cfg: dict | None = None) -> list[dict]:
    """One contiguous history -> list of triggered setups with entry / SL / T1-3."""
    c = cfg or merged()
    if len(bars) < c["warmup_bars"] + 5:
        return []
    frame = build_signals(bars, c)
    setups = []
    for row in frame:
        i = row["i"]
        if i < c["warmup_bars"]:
            continue
        a = row["atr"]
        if a is None or a <= 0:
            continue
        if row["buy_signal"]:
            side = "LONG"
        elif row["sell_signal"]:
            side = "SHORT"
        else:
            continue

        # ---- Stage-2 confirmation/filter layer (post-signal; does NOT touch the
        #      SSL signal, only drops triggered setups). All default OFF. ----
        if not _passes_filters(row, side, c):
            continue

        entry = row["close"]
        risk = a * c["atr_mult"]
        if risk <= 0:
            continue
        if side == "LONG":
            sl = entry - risk
            t1, t2, t3 = (entry + risk * c["rr1"], entry + risk * c["rr2"], entry + risk * c["rr3"])
        else:
            sl = entry + risk
            t1, t2, t3 = (entry - risk * c["rr1"], entry - risk * c["rr2"], entry - risk * c["rr3"])
        setups.append({
            "entry_index": i, "side": side, "session_date": row["session_date"],
            "entry_hhmm": row["hhmm"], "entry": round(entry, 2), "stop": round(sl, 2),
            "risk_points": round(risk, 2), "t1": round(t1, 2), "t2": round(t2, 2),
            "t3": round(t3, 2), "bull_score": row["bull_score"], "bear_score": row["bear_score"],
        })
    return setups
