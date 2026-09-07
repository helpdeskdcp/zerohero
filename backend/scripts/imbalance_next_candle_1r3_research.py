#!/usr/bin/env python3
"""
imbalance_next_candle_1r3_research.py  --  RESEARCH / BACKTEST ONLY.

Extension of the IMBALANCE_NEXT_CANDLE_1R3 research line (see
ORDERFLOW_L2_IMBALANCE_GATE.md). Adds: multi-timeframe (1m/5m/30m) confirmation,
sideways -> abnormal-spike conditioning with a threshold sweep, the N+1
stop-entry / 1:2-1:3-1:4 / 25-minute-timeout mechanics, expiry-day index
OI / OI-change conditioning, PCR, support/resistance/pivot proximity, and
strike-level option-premium recording.

READ-ONLY. Separate module. Changes NOTHING on the frozen H1/H7 classifier, the
live trading / entry / SL / target / risk logic, broker / execution, or
production signal generation. Emits no order. Writes only CSVs under data/
(git-ignored).

=====================================================================
GENUINE L2 REQUIREMENT (spec section 11) -- decided up front
=====================================================================
This environment has NO aggressor-classified trade data: no tick feed, no
per-trade side, no trade delta, no order-book event stream (see
ORDERFLOW_STAGE9_L2_RESEARCH.md; the Angel mode-3 SnapQuote capture that was
just wired carries best-5 depth but STILL no aggressor side). Therefore every
"imbalance" measured here is a PROXY and is labelled as such:

  * passive_book : resting bid_qty / ask_qty from ~25-30 s quote snapshots
                   (RESTING liquidity, not aggressor flow) -- 3 futures sessions.
  * vol_range    : an abnormal candle = range >= k*median AND volume >= m*median,
                   direction = candle colour (an OHLC+VOLUME proxy).
  * range_only   : range >= k*median only (cash index, no volume) -- shape proxy.

Per spec section 11 the headline result is fixed:
    NOT VALIDATED -- GENUINE L2 REQUIRED.
Nothing below promotes a proxy to genuine L2. Nothing is PROVEN.
"""
from __future__ import annotations

import argparse
import csv as _csv
import json
import sqlite3
import statistics as st
import sys
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HDB = ROOT / "data" / "market_history.db"
UDB = ROOT / "data" / "historical" / "upstox" / "upstox_research.db"
KAGGLE_NIFTY = (ROOT / "data" / "historical" / "kaggle" /
                "debashis74017__nifty-50-minute-data" / "NIFTY_50_5minute.csv")
OUT_EVENTS = ROOT / "data" / "imbalance_next_candle_1r3_events.csv"

_IST = timezone(timedelta(hours=5, minutes=30))
FUT_SYMS = ("NIFTY", "CRUDEOIL", "NATURALGAS")
TF_MIN = {"1M": 1, "5M": 5, "30M": 30}
THRESHOLDS = (2.0, 3.0, 4.0, 5.0)                 # 200 / 300 / 400 / 500 %
TARGETS = (2.0, 3.0, 4.0)                         # 1:2 / 1:3 (primary) / 1:4
MAX_LIFETIME_MIN = 25
ROLL = 20                                         # rolling baseline window (bars)
COMPRESS_LOOKBACK = 12                            # pre-imbalance compression window


# ============================================================ loaders
def _ist_bucket(ts_utc_iso: str, minutes: int) -> datetime:
    t = datetime.fromisoformat(ts_utc_iso.replace("Z", "+00:00")).astimezone(_IST)
    floored = t - timedelta(minutes=t.minute % minutes, seconds=t.second, microseconds=t.microsecond)
    return floored


def load_fut_1m(con):
    """{sym: {date: [ {t(IST dt), o,h,l,c,v} 1-min, ascending ] }} from market_candles FUTURE."""
    out: dict = {}
    rows = con.execute(
        "SELECT symbol, session_date_ist, bar_start, o,h,l,c,v FROM market_candles "
        "WHERE kind='FUTURE' AND tf='1m' AND symbol IN (?,?,?) ORDER BY symbol, bar_start",
        FUT_SYMS).fetchall()
    for sym, d, bs, o, h, l, c, v in rows:
        if None in (o, h, l, c):
            continue
        t = datetime.fromisoformat(bs.replace("Z", "+00:00")).astimezone(_IST)
        if not ("09:00" <= t.strftime("%H:%M") <= "23:59"):
            continue
        out.setdefault(sym, {}).setdefault(d, []).append(
            {"t": t, "o": float(o), "h": float(h), "l": float(l), "c": float(c),
             "v": float(v or 0.0)})
    return out


def _src_minutes(bars):
    if len(bars) < 3:
        return 1
    gaps = sorted((bars[i + 1]["t"] - bars[i]["t"]).total_seconds() for i in range(min(6, len(bars) - 1)))
    return max(1, round(gaps[len(gaps) // 2] / 60))


def resample(src, minutes):
    """src bars -> `minutes`-min bars. A bar is emitted only once its window is
    COMPLETE (>=60% of the expected source bars present -- no partial/future
    candle). Source cadence is inferred, so 5m->5m is identity and 5m->30m needs
    ~4 of 6 source bars. Returns [ {t,o,h,l,c,v,closed_at} ]."""
    sm = _src_minutes(src)
    if minutes <= sm:
        return [dict(b, closed_at=b["t"] + timedelta(minutes=sm)) for b in src]
    need = max(1, int(round(minutes / sm * 0.6)))
    buckets: dict = {}
    for b in src:
        key = b["t"] - timedelta(minutes=b["t"].minute % minutes,
                                 seconds=b["t"].second, microseconds=b["t"].microsecond)
        g = buckets.setdefault(key, {"t": key, "o": b["o"], "h": b["h"], "l": b["l"],
                                     "c": b["c"], "v": 0.0, "n": 0})
        g["h"] = max(g["h"], b["h"]); g["l"] = min(g["l"], b["l"])
        g["c"] = b["c"]; g["v"] += b["v"]; g["n"] += 1
    out = []
    for key in sorted(buckets):
        g = buckets[key]
        if g["n"] >= need:
            out.append({"t": g["t"], "o": g["o"], "h": g["h"], "l": g["l"],
                        "c": g["c"], "v": g["v"], "closed_at": g["t"] + timedelta(minutes=minutes)})
    return out


def load_book_series(con):
    """{sym: {date: [ (t_ist, bidq/askq ratio, sum5 ratio, bid, ask, ltp) ] }} from quote_snapshots."""
    out: dict = {}
    rows = con.execute(
        "SELECT symbol, session_date_ist, received_ts, exch_ts, ltp, bid, ask, bid_qty, ask_qty, depth_json "
        "FROM quote_snapshots WHERE kind='FUTURE' AND symbol IN (?,?,?) "
        "AND bid_qty>0 AND ask_qty>0 ORDER BY symbol, received_ts", FUT_SYMS).fetchall()
    for sym, d, rts, ets, ltp, bid, ask, bq, aq, dj in rows:
        try:
            t = datetime.fromisoformat((ets or rts).replace("Z", "+00:00")).astimezone(_IST)
        except (ValueError, AttributeError):
            continue
        if None in (bid, ask) or bid <= 0 or ask <= 0 or bid > ask:
            continue
        r1 = bq / aq
        r5 = None
        if dj and dj not in ("", "null", "{}"):
            try:
                dd = json.loads(dj)
                sb = sum(x.get("quantity") or 0 for x in dd.get("buy") or [] if (x.get("price") or 0) > 0)
                ss = sum(x.get("quantity") or 0 for x in dd.get("sell") or [] if (x.get("price") or 0) > 0)
                r5 = (sb / ss) if ss > 0 else None
            except (ValueError, TypeError):
                r5 = None
        out.setdefault(sym, {}).setdefault(d, []).append(
            (t, r1, r5, float(bid), float(ask), float(ltp) if ltp else None))
    return out


def _asof(series_ts, series_val, t):
    i = bisect_right(series_ts, t) - 1
    return series_val[i] if i >= 0 else None


def load_option_oi(con):
    """{underlying: {date: [ (t_ist, {strike: (ce_oi, pe_oi)} ) ] }} sampled ~30s.
    Also the expiry string per underlying."""
    out: dict = {}
    exp: dict = {}
    rows = con.execute(
        "SELECT symbol, session_date_ist, received_ts, exch_ts, strike, option_type, oi, expiry "
        "FROM quote_snapshots WHERE kind='OPTION' AND oi IS NOT NULL ORDER BY symbol, received_ts").fetchall()
    tmp: dict = {}
    for sym, d, rts, ets, strike, ot, oi, expiry in rows:
        if strike is None or ot not in ("CE", "PE"):
            continue
        exp.setdefault(sym, set()).add(expiry)
        try:
            t = datetime.fromisoformat((ets or rts).replace("Z", "+00:00")).astimezone(_IST)
        except (ValueError, AttributeError):
            continue
        sec = t.replace(microsecond=0)
        key = (sym, d, sec)
        g = tmp.setdefault(key, {})
        ce, pe = g.get(float(strike), (None, None))
        if ot == "CE":
            ce = float(oi)
        else:
            pe = float(oi)
        g[float(strike)] = (ce, pe)
    for (sym, d, sec), g in sorted(tmp.items()):
        out.setdefault(sym, {}).setdefault(d, []).append((sec, g))
    return out, {k: sorted(v) for k, v in exp.items()}


_UP_5M_CACHE: dict = {}


def upstox_atm_prem(underlying, t_ist, spot):
    """Best-effort ATM CE/PE premium from the Upstox expired-options 5m view at
    (t_ist, spot). Returns {ce: {...}, pe: {...}} with entry/MFE/MAE over the next
    25 min, or {} if unavailable. NIFTY only."""
    if underlying != "NIFTY" or not UDB.exists() or spot is None:
        return {}
    key = t_ist.date().isoformat()
    if key not in _UP_5M_CACHE:
        con = sqlite3.connect(f"file:{UDB}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT instrument_key, strike, option_type, timestamp, open, high, low, close "
                "FROM option_bars_5m WHERE substr(timestamp,1,10)=? ", (key,)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        con.close()
        by: dict = {}
        for ik, k, ot, ts, o, h, l, c in rows:
            by.setdefault((round(k), ot), []).append(
                (datetime.fromisoformat(ts).astimezone(_IST), o, h, l, c))
        for v in by.values():
            v.sort()
        _UP_5M_CACHE[key] = by
    by = _UP_5M_CACHE[key]
    if not by:
        return {}
    strikes = sorted({k for (k, _ot) in by})
    if not strikes:
        return {}
    atm = min(strikes, key=lambda s: abs(s - spot))
    res = {}
    for ot in ("CE", "PE"):
        seq = by.get((atm, ot))
        if not seq:
            continue
        i = bisect_right([x[0] for x in seq], t_ist) - 1
        if i < 0:
            continue
        entry = seq[i][4]
        window = [x for x in seq[i + 1:] if x[0] <= t_ist + timedelta(minutes=MAX_LIFETIME_MIN)]
        mfe = max((x[2] for x in window), default=entry) - entry
        mae = min((x[3] for x in window), default=entry) - entry
        res[ot] = {"strike": atm, "entry": round(entry, 2),
                   "mfe": round(mfe, 2), "mae": round(mae, 2)}
    return res


def load_kaggle_nifty_5m():
    """{date: [ {t,o,h,l,c,v=0} 5m ]} cash index, regular session only."""
    if not KAGGLE_NIFTY.exists():
        return {}
    out: dict = {}
    with KAGGLE_NIFTY.open() as f:
        for r in _csv.DictReader(f):
            try:
                dt = datetime.strptime(r["date"], "%Y-%m-%d %H:%M:%S")
                o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            except (ValueError, KeyError):
                continue
            if not ("09:15" <= dt.strftime("%H:%M") <= "15:30"):
                continue
            if not (o > 0 and h >= l and l > 0):
                continue
            out.setdefault(dt.date().isoformat(), []).append(
                {"t": dt.replace(tzinfo=_IST), "o": o, "h": h, "l": l, "c": c, "v": 0.0})
    for d in out:
        out[d].sort(key=lambda b: b["t"])
    return out


# ============================================================ features
def colour(b):
    return "GREEN" if b["c"] >= b["o"] else "RED"


def _pivots(prev_h, prev_l, prev_c):
    p = (prev_h + prev_l + prev_c) / 3
    return {
        "PIVOT": p,
        "R1": 2 * p - prev_l, "S1": 2 * p - prev_h,
        "R2": p + (prev_h - prev_l), "S2": p - (prev_h - prev_l),
        "R3": prev_h + 2 * (p - prev_l), "S3": prev_l - 2 * (prev_h - p),
    }


def _swings(bars, i, k=2):
    """most recent confirmed 3-bar swing high / low strictly before bar i."""
    hi = lo = None
    for j in range(i - k - 1, k, -1):
        if hi is None and bars[j]["h"] >= bars[j - 1]["h"] and bars[j]["h"] >= bars[j + 1]["h"]:
            hi = bars[j]["h"]
        if lo is None and bars[j]["l"] <= bars[j - 1]["l"] and bars[j]["l"] <= bars[j + 1]["l"]:
            lo = bars[j]["l"]
        if hi and lo:
            break
    return hi, lo


def spike_feats(bars, i):
    """range compression before N, and expansion multiples at N."""
    if i < ROLL:
        return {}
    rng = [b["h"] - b["l"] for b in bars[i - ROLL:i]]
    vol = [b["v"] for b in bars[i - ROLL:i]]
    med_r = st.median(rng) or 1e-9
    med_v = st.median(vol) if any(vol) else 0.0
    pre = [b["h"] - b["l"] for b in bars[i - COMPRESS_LOOKBACK:i]]
    b = bars[i]
    hi20 = max(x["h"] for x in bars[i - ROLL:i])
    lo20 = min(x["l"] for x in bars[i - ROLL:i])
    return {
        "range_x": (b["h"] - b["l"]) / med_r,
        "vol_x": (b["v"] / med_v) if med_v > 0 else None,
        "pre_compression": (st.median(pre) / med_r) if pre else None,     # <1 = coiled
        "dist_above_range": max(0.0, b["h"] - hi20),
        "dist_below_range": max(0.0, lo20 - b["l"]),
    }


# ============================================================ N+1 walk (25-min cap)
def walk_n1(bars_1m, n1_close_t, n1, rr):
    """N+1 stop-entry mechanics. green -> buy-stop @ n1.h, SL @ n1.l ; red mirror.
    Walk 1-min bars from just after N+1 close, hard 25-minute lifetime.
    """
    R = n1["h"] - n1["l"]
    if R <= 0:
        return None
    green = colour(n1) == "GREEN"
    entry = n1["h"] if green else n1["l"]
    sl = n1["l"] if green else n1["h"]
    tgt = entry + rr * R if green else entry - rr * R
    deadline = n1_close_t + timedelta(minutes=MAX_LIFETIME_MIN)
    seg = [b for b in bars_1m if n1_close_t < b["t"] <= deadline]
    if not seg:
        return {"outcome": "NO_DATA"}
    entered = False
    t_entry = None
    mfe = mae = 0.0
    last_c = None
    for b in seg:
        if not entered:
            hit = (b["h"] >= entry) if green else (b["l"] <= entry)
            if hit:
                entered = True
                t_entry = b["t"]
            else:
                continue
        last_c = b["c"]
        adv = (b["h"] - entry) if green else (entry - b["l"])
        adc = (b["l"] - entry) if green else (entry - b["h"])
        mfe = max(mfe, adv); mae = min(mae, adc)
        hit_sl = (b["l"] <= sl) if green else (b["h"] >= sl)
        hit_tg = (b["h"] >= tgt) if green else (b["l"] <= tgt)
        if hit_sl and hit_tg:
            hit_tg = False                        # pessimistic: SL first on a straddling bar
        if hit_sl:
            return _res("SL", entry, sl, R, rr, t_entry, b["t"], n1_close_t, mfe, mae, green)
        if hit_tg:
            return _res("TARGET", entry, tgt, R, rr, t_entry, b["t"], n1_close_t, mfe, mae, green)
    if not entered:
        return {"outcome": "NO_TRIGGER"}
    # TIMEOUT: mark-to-market flat exit at the 25-min bar close (honest, not the excursion)
    return _res("TIMEOUT", entry, None, R, rr, t_entry, deadline, n1_close_t, mfe, mae, green,
                mtm=(last_c if last_c is not None else entry))


def _res(outcome, entry, exitp, R, rr, t_entry, t_exit, t0, mfe, mae, green, mtm=None):
    if outcome == "TARGET":
        realized = rr
    elif outcome == "SL":
        realized = -1.0
    else:  # TIMEOUT -> flat exit at 25-min close, marked to market
        realized = round(((mtm - entry) if green else (entry - mtm)) / R, 3) if mtm is not None else 0.0
    return {
        "outcome": outcome, "entry": round(entry, 3), "R_pts": round(R, 3),
        "dist_to_entry": None,   # filled by caller (needs N close)
        "t_to_entry_s": (t_entry - t0).total_seconds() if t_entry else None,
        "dist_entry_to_tgt": round(rr * R, 3),
        "t_to_target_s": (t_exit - t0).total_seconds() if outcome == "TARGET" else None,
        "dist_to_sl": round(R, 3),
        "t_to_sl_s": (t_exit - t0).total_seconds() if outcome == "SL" else None,
        "MFE_R": round(mfe / R, 3), "MAE_R": round(mae / R, 3),
        "realized_R": realized,
    }


# ============================================================ event build
def build_events(fut1m, book, opt_oi, opt_exp, kaggle):
    ev = []

    # ---- futures: passive_book + vol_range proxy, MTF ----
    for sym, days in fut1m.items():
        for d, b1 in sorted(days.items()):
            if len(b1) < ROLL + 5:
                continue
            tfbars = {tf: resample(b1, m) for tf, m in TF_MIN.items()}
            tf_ts = {tf: [x["t"] for x in tfbars[tf]] for tf in TF_MIN}
            bs = book.get(sym, {}).get(d) or []
            b_ts = [x[0] for x in bs]
            b_r1 = [x[1] for x in bs]
            prev_hlc = _prev_session_hlc(days, d)
            piv = _pivots(*prev_hlc) if prev_hlc else None
            for tf, bars in tfbars.items():
                for i in range(ROLL, len(bars) - 1):
                    N, N1 = bars[i], bars[i + 1]
                    feats = spike_feats(bars, i)
                    if not feats:
                        continue
                    # --- proxy imbalance ratios at N ---
                    pb = _asof(b_ts, b_r1, N["closed_at"]) if bs else None
                    pb_dir_ratio = None
                    if pb is not None:
                        pb_dir_ratio = pb if colour(N) == "GREEN" else (1.0 / pb if pb > 0 else None)
                    vr_ratio = feats.get("vol_x")
                    for proxy, ratio in (("passive_book", pb_dir_ratio), ("vol_range", vr_ratio)):
                        if ratio is None or ratio < THRESHOLDS[0]:
                            continue
                        if proxy == "vol_range" and feats["range_x"] < 1.5:
                            continue
                        ev.append(_mk_event(
                            "fut", sym, d, tf, proxy, ratio, N, N1, bars, i, feats, piv,
                            b1, opt_oi, opt_exp, tfbars, tf_ts))
    # ---- kaggle NIFTY 5m: range_only shape proxy, many sessions (MTF: 5M/30M) ----
    if kaggle:
        for d, b5 in sorted(kaggle.items()):
            if len(b5) < ROLL + 5:
                continue
            tfbars = {"5M": resample(b5, 5), "30M": resample(b5, 30)}
            tf_ts = {tf: [x["t"] for x in tfbars[tf]] for tf in tfbars}
            prev_hlc = _prev_session_hlc(kaggle, d)
            piv = _pivots(*prev_hlc) if prev_hlc else None
            for tf, bars in tfbars.items():
                for i in range(ROLL, len(bars) - 1):
                    N, N1 = bars[i], bars[i + 1]
                    feats = spike_feats(bars, i)
                    if not feats or feats["range_x"] < THRESHOLDS[0]:
                        continue
                    ev.append(_mk_event("kaggle_nifty", "NIFTY", d, tf, "range_only",
                                        feats["range_x"], N, N1, bars, i, feats, piv,
                                        b5, {}, {}, tfbars, tf_ts))
    return ev


def _prev_session_hlc(days, d):
    ds = sorted(days)
    k = ds.index(d)
    if k == 0:
        return None
    p = days[ds[k - 1]]
    return max(x["h"] for x in p), min(x["l"] for x in p), p[-1]["c"]


def _bucket(r):
    return (">=500%" if r >= 5 else ">=400%" if r >= 4 else
            ">=300%" if r >= 3 else ">=200%")


def _mtf_confirm(tfbars, tf_ts, at_t, want_dir):
    """same-direction proxy imbalance on each OTHER timeframe, using only bars
    whose window closed <= at_t (no future candle). Returns set of confirming TFs."""
    ok = set()
    for tf, bars in tfbars.items():
        j = bisect_right(tf_ts[tf], at_t) - 1
        for jj in range(j, max(-1, j - 4), -1):
            if jj < ROLL:
                break
            b = bars[jj]
            if b.get("closed_at") and b["closed_at"] > at_t:
                continue
            f = spike_feats(bars, jj)
            if not f:
                continue
            rx = f["range_x"]
            cdir = colour(b)
            if rx >= 2.0 and cdir == want_dir:
                ok.add(tf)
                break
    return ok


def _mk_event(ds, sym, d, tf, proxy, ratio, N, N1, bars, i, feats, piv, base1m,
              opt_oi, opt_exp, tfbars, tf_ts):
    dirn = colour(N1)                                  # trade direction = N+1 colour
    n1_range = N1["h"] - N1["l"]
    # S/R distances from N close
    lv = dict(piv or {})
    shi, slo = _swings(bars, i)
    if shi:
        lv["SWING_HI"] = shi
    if slo:
        lv["SWING_LO"] = slo
    dists = {f"d_{k}": round(N["c"] - v, 3) for k, v in lv.items()}
    near = min((abs(N["c"] - v) for v in lv.values()), default=None)
    # MTF confirm at N close
    conf = _mtf_confirm(tfbars, tf_ts, N.get("closed_at") or N["t"], colour(N))
    conf.discard(tf)
    # expiry / OI / PCR (only where option OI exists for this underlying)
    exp_list = opt_exp.get(sym, [])
    is_expiry = _is_expiry_day(exp_list, d)
    oi = _oi_snapshot(opt_oi.get(sym, {}).get(d), N.get("closed_at") or N["t"], N["c"]) if opt_oi.get(sym) else {}
    # option premium (NIFTY futures only)
    prem = {}
    if ds == "fut" and sym == "NIFTY":
        prem = upstox_atm_prem("NIFTY", N["t"], N["c"])
    rr_res = {}
    for rr in TARGETS:
        w = walk_n1(base1m, N1["closed_at"] if "closed_at" in N1 else N1["t"], N1, rr)
        if w and w.get("outcome") not in ("NO_DATA",):
            if "entry" in w:
                w["dist_to_entry"] = round(abs(w["entry"] - N["c"]), 3)
            rr_res[f"rr{int(rr)}"] = w
    return {
        "dataset": ds, "symbol": sym, "session": d, "tf": tf, "proxy": proxy,
        "imb_ratio": round(ratio, 3), "imb_bucket": _bucket(ratio),
        "imb_colour": colour(N), "n1_colour": dirn, "direction": dirn,
        "n1_high": N1["h"], "n1_low": N1["l"], "n1_range": round(n1_range, 3),
        "dist_N_to_n1_high": round(N1["h"] - N["c"], 3),
        "dist_N_to_n1_low": round(N["c"] - N1["l"], 3),
        "range_x": round(feats["range_x"], 3), "vol_x": feats.get("vol_x"),
        "pre_compression": feats.get("pre_compression"),
        "dist_above_range": feats["dist_above_range"], "dist_below_range": feats["dist_below_range"],
        "mtf_confirm": "+".join(sorted(conf)) or "-", "n_mtf_confirm": len(conf),
        "near_level_dist": round(near, 3) if near is not None else None,
        **dists,
        "is_expiry_day": is_expiry,
        **{f"oi_{k}": v for k, v in oi.items()},
        **{f"prem_{ot}_{k}": v for ot, dd in prem.items() for k, v in dd.items()},
        "rr_res": rr_res,
    }


def _is_expiry_day(exp_list, d):
    for e in exp_list:
        for f in ("%d%b%Y", "%d-%b-%Y"):
            try:
                if datetime.strptime(e.upper(), f).date().isoformat() == d:
                    return True
            except ValueError:
                pass
    return False


def _oi_snapshot(series, t, spot):
    if not series:
        return {}
    i = bisect_right([x[0].replace(tzinfo=_IST) if x[0].tzinfo is None else x[0] for x in series],
                     t if t.tzinfo else t.replace(tzinfo=_IST)) - 1
    if i < 1:
        return {}
    _, g = series[i]
    _, g0 = series[max(0, i - 10)]                        # ~5 min earlier
    strikes = sorted(g)
    if not strikes:
        return {}
    atm = min(strikes, key=lambda s: abs(s - spot))
    zone = [s for s in strikes if abs(s - atm) <= 3 * _step(strikes)]
    ce = sum(g[s][0] or 0 for s in strikes)
    pe = sum(g[s][1] or 0 for s in strikes)
    ce0 = sum(g0.get(s, (0, 0))[0] or 0 for s in strikes)
    pe0 = sum(g0.get(s, (0, 0))[1] or 0 for s in strikes)
    ce_z = sum(g[s][0] or 0 for s in zone)
    pe_z = sum(g[s][1] or 0 for s in zone)
    return {
        "ce": ce, "pe": pe, "d_ce": ce - ce0, "d_pe": pe - pe0,
        "pcr": round(pe / ce, 3) if ce > 0 else None,
        "pcr_prev": round(pe0 / ce0, 3) if ce0 > 0 else None,
        "atm": atm, "atm_zone_ce": ce_z, "atm_zone_pe": pe_z,
        "atm_conc": round((ce_z + pe_z) / (ce + pe), 3) if (ce + pe) > 0 else None,
    }


def _step(strikes):
    gaps = sorted({round(strikes[i + 1] - strikes[i], 4) for i in range(len(strikes) - 1)
                   if strikes[i + 1] > strikes[i]})
    return gaps[0] if gaps else 50.0


# ============================================================ aggregate
def agg(recs, rr_key):
    w = [r["rr_res"][rr_key] for r in recs if rr_key in r["rr_res"]]
    trig = [x for x in w if x["outcome"] in ("TARGET", "SL", "TIMEOUT")]
    tgt = [x for x in trig if x["outcome"] == "TARGET"]
    sl = [x for x in trig if x["outcome"] == "SL"]
    to = [x for x in trig if x["outcome"] == "TIMEOUT"]
    n = len(trig)
    if not n:
        return {"signals": len(recs), "triggered": 0}
    rz = [x["realized_R"] for x in trig]
    wins = [x for x in rz if x > 0]
    losses = [x for x in rz if x < 0]
    # max consec losses chronological
    mcl = cur = 0
    for r in sorted(recs, key=lambda r: (r["session"], r.get("tf", ""))):
        x = r["rr_res"].get(rr_key)
        if not x or x["outcome"] not in ("TARGET", "SL", "TIMEOUT"):
            continue
        if x["realized_R"] < 0:
            cur += 1; mcl = max(mcl, cur)
        elif x["realized_R"] > 0:
            cur = 0
    tte = [x["t_to_entry_s"] for x in w if x.get("t_to_entry_s") is not None]
    ttt = [x["t_to_target_s"] for x in tgt if x.get("t_to_target_s") is not None]
    tts = [x["t_to_sl_s"] for x in sl if x.get("t_to_sl_s") is not None]
    return {
        "signals": len(recs), "triggered": n,
        "target_hits": len(tgt), "sl_hits": len(sl), "timeouts": len(to),
        "target_hit_pct": round(len(tgt) / n, 3), "sl_pct": round(len(sl) / n, 3),
        "timeout_pct": round(len(to) / n, 3),
        "win_rate": round(len(wins) / n, 3),
        "avg_R": round(st.fmean(rz), 3), "expectancy": round(st.fmean(rz), 3),
        "profit_factor": round(sum(wins) / -sum(losses), 3) if losses else None,
        "avg_MFE_R": round(st.fmean(x["MFE_R"] for x in trig), 3),
        "avg_MAE_R": round(st.fmean(x["MAE_R"] for x in trig), 3),
        "max_consec_losses": mcl,
        "t_to_entry_med_s": round(st.median(tte), 1) if tte else None,
        "t_to_target_med_s": round(st.median(ttt), 1) if ttt else None,
        "t_to_sl_med_s": round(st.median(tts), 1) if tts else None,
        "sessions": len({r["session"] for r in recs}),
    }


def _line(tag, m):
    if not m or not m.get("triggered"):
        return f"  {tag:<44} sig={m.get('signals', 0):>4} trig=0"
    return (f"  {tag:<44} sig={m['signals']:>4} trig={m['triggered']:>4} ses={m['sessions']} "
            f"tgt%={m['target_hit_pct']*100:>5.1f} sl%={m['sl_pct']*100:>5.1f} to%={m['timeout_pct']*100:>5.1f} "
            f"E[R]={m['expectancy']:>6.2f} PF={m['profit_factor']} "
            f"MFE~{m['avg_MFE_R']:>5.2f} MAE~{m['avg_MAE_R']:>6.2f} mcl={m['max_consec_losses']}")


def _split(recs):
    ds = sorted({r["session"] for r in recs})
    if len(ds) < 3:
        return {d: "ALL" for d in ds}
    a, b = int(len(ds) * 0.5), int(len(ds) * 0.75)
    return {d: ("TRAIN" if k < a else "VALIDATION" if k < b else "OOS")
            for k, d in enumerate(ds)}


# ============================================================ report
def report(ev, out):
    p = lambda *a: print(*a, file=out)
    p("=" * 108)
    p("IMBALANCE_NEXT_CANDLE_1R3  --  MULTI-TIMEFRAME + SPIKE + OI/PCR + S/R  RESEARCH (READ-ONLY, PROXY)")
    p("Genuine aggressor-side L2 data does NOT exist here. Every 'imbalance' below is a PROXY")
    p("(passive_book = resting bid/ask size ratio; vol_range = range&volume abnormality; range_only =")
    p("range abnormality on a volume-less index). Per spec section 11 the headline result is fixed:")
    p("        NOT VALIDATED -- GENUINE L2 REQUIRED.   Nothing here is PROVEN or production-bound.")
    p("=" * 108)

    for ds in ("fut", "kaggle_nifty"):
        sub = [r for r in ev if r["dataset"] == ds]
        if not sub:
            continue
        sess = sorted({r["session"] for r in sub})
        p(f"\nDATASET {ds}: {len(sub)} proxy events | {len(sess)} sessions {sess[0]}..{sess[-1]} | "
          f"symbols {sorted({r['symbol'] for r in sub})} | proxies {sorted({r['proxy'] for r in sub})}")
        spl = _split(sub)

        p("  -- primary 1:3, per proxy x CUMULATIVE threshold (ratio >= T) --")
        for proxy in sorted({r["proxy"] for r in sub}):
            for thr, lab in zip(THRESHOLDS, ("200%", "300%", "400%", "500%")):
                rr = [r for r in sub if r["proxy"] == proxy and r["imb_ratio"] >= thr]
                if rr:
                    p(_line(f"{proxy} >= {lab}", agg(rr, "rr3")))

        p("  -- 1:3 by timeframe config (A-G) --")
        for cfg, sel in _tf_configs():
            rr = [r for r in sub if sel(r)]
            if rr:
                p(_line(f"TF {cfg}", agg(rr, "rr3")))

        p("  -- 1:3 by target 1:2 / 1:3 / 1:4 (pooled, >=200%) --")
        base = [r for r in sub if r["imb_bucket"] != "" ]
        for rrk, lab in (("rr2", "1:2"), ("rr3", "1:3"), ("rr4", "1:4")):
            p(_line(f"target {lab}", agg(base, rrk)))

        p("  -- 1:3 normal vs sideways->spike (pre_compression<0.8 & range_x>=2) --")
        norm = [r for r in sub if not (_is_squeeze(r))]
        sq = [r for r in sub if _is_squeeze(r)]
        p(_line("NORMAL", agg(norm, "rr3")))
        p(_line("SIDEWAYS->SPIKE", agg(sq, "rr3")))

        p("  -- 1:3 near a structural level vs away (near_level_dist within 0.15% of price) --")
        nearr = [r for r in sub if _is_near(r)]
        far = [r for r in sub if not _is_near(r)]
        p(_line("near S/R/pivot", agg(nearr, "rr3")))
        p(_line("away", agg(far, "rr3")))

        if any(r.get("oi_pcr") is not None for r in sub):
            p("  -- 1:3 expiry vs non-expiry (option-OI available) --")
            p(_line("EXPIRY day", agg([r for r in sub if r["is_expiry_day"]], "rr3")))
            p(_line("NON-EXPIRY", agg([r for r in sub if not r["is_expiry_day"]], "rr3")))
            p("  -- 1:3 by OI/PCR condition at the event --")
            p(_line("d_CE>0 (CE OI rising)", agg([r for r in sub if (r.get("oi_d_ce") or 0) > 0], "rr3")))
            p(_line("d_CE<0", agg([r for r in sub if (r.get("oi_d_ce") or 0) < 0], "rr3")))
            p(_line("d_PE>0 (PE OI rising)", agg([r for r in sub if (r.get("oi_d_pe") or 0) > 0], "rr3")))
            p(_line("d_PE<0", agg([r for r in sub if (r.get("oi_d_pe") or 0) < 0], "rr3")))
            p(_line("PCR>=1.0", agg([r for r in sub if (r.get("oi_pcr") or 0) >= 1.0], "rr3")))
            p(_line("PCR<1.0", agg([r for r in sub if 0 < (r.get("oi_pcr") or 0) < 1.0], "rr3")))
            p(_line("PCR rising vs prev", agg([r for r in sub if r.get("oi_pcr") and r.get("oi_pcr_prev")
                                               and r["oi_pcr"] > r["oi_pcr_prev"]], "rr3")))

        p("  -- 1:3 chronological split --")
        for s in ("TRAIN", "VALIDATION", "OOS", "ALL"):
            rr = [r for r in sub if spl.get(r["session"]) == s]
            if rr:
                p(_line(s, agg(rr, "rr3")))
        oos_d = sorted(d for d, s in spl.items() if s == "OOS")
        p(f"  OOS = {len(oos_d)} sessions" + (f"  {oos_d[0]}..{oos_d[-1]}" if oos_d else ""))
        p("  latest-OOS-quarter (last 20% of OOS sessions):")
        if len(oos_d) >= 5:
            latest = set(oos_d[int(len(oos_d) * 0.8):])
            p(_line("  latest OOS", agg([r for r in sub if r["session"] in latest], "rr3")))

        if any(r.get("prem_CE_mfe") is not None for r in sub):
            pm = [r for r in sub if r.get("prem_CE_mfe") is not None]
            p(f"  -- option-premium (NIFTY ATM, best-effort, n={len(pm)}): "
              f"median CE MFE={_med(pm,'prem_CE_mfe')} MAE={_med(pm,'prem_CE_mae')} | "
              f"PE MFE={_med(pm,'prem_PE_mfe')} MAE={_med(pm,'prem_PE_mae')}  (research only, target formula unchanged)")

    _verdict(ev, p)


def _med(rows, k):
    v = [r[k] for r in rows if r.get(k) is not None]
    return round(st.median(v), 2) if v else None


def _tf_configs():
    return [
        ("A 1M only", lambda r: r["tf"] == "1M"),
        ("B 5M only", lambda r: r["tf"] == "5M"),
        ("C 30M only", lambda r: r["tf"] == "30M"),
        ("D 1M+5M agree", lambda r: r["tf"] == "1M" and "5M" in r["mtf_confirm"]),
        ("E 1M+30M agree", lambda r: r["tf"] == "1M" and "30M" in r["mtf_confirm"]),
        ("F 5M+30M agree", lambda r: r["tf"] == "5M" and "30M" in r["mtf_confirm"]),
        ("G 1M+5M+30M agree", lambda r: r["tf"] == "1M" and "5M" in r["mtf_confirm"] and "30M" in r["mtf_confirm"]),
    ]


def _is_squeeze(r):
    pc = r.get("pre_compression")
    return pc is not None and pc < 0.8 and r["range_x"] >= 2.0


def _is_near(r):
    d = r.get("near_level_dist")
    ref = abs(r["n1_high"]) or 1.0
    return d is not None and abs(d) <= 0.0015 * ref


def _verdict(ev, p):
    p("\n" + "=" * 108)
    p("VERDICT  (spec sections 10-12)")
    p("=" * 108)
    fut = [r for r in ev if r["dataset"] == "fut"]
    fsess = sorted({r["session"] for r in fut})
    pb = [r for r in fut if r["proxy"] == "passive_book"]
    pbsess = sorted({r["session"] for r in pb})
    p(f"  N. GENUINE L2?  NO. No aggressor-side data exists (tick / trade-side / delta / book-event).")
    p(f"     => NOT VALIDATED -- GENUINE L2 REQUIRED.  The closest proxy (passive_book) has "
      f"{len(pb)} events over {len(pbsess)} sessions {pbsess} -- one week, one regime, ~30s snapshots.")
    p(f"  L. OOS sample size (futures proxy): TRAIN/VAL/OOS split over {len(fsess)} sessions is degenerate; "
      f"the OOS slice is a single day. A real 45/20/17/18 split is impossible.")
    p("  A-K. The per-question tables above are PROXY observations only. With <=4 sessions and one")
    p("       regime none of them is a validated answer; any positive cell is within-noise and is")
    p("       explicitly NOT selected (spec section 10). Multi-timeframe, sideways->spike, S/R,")
    p("       expiry OI, and PCR are all reported as NON-CONTRIBUTORY-UNTESTABLE on this data.")
    p("  M. 25-minute outcomes are in the tables (target_hits / sl_hits / timeouts columns).")
    p("  => No configuration is promoted. No production wiring. Frozen H1/H7 + live trading logic")
    p("     untouched. Re-run this module once the Angel mode-3 SnapQuote capture (or a real")
    p("     aggressor feed) has accumulated >= 40 sessions across >= 2 regimes.")


# ============================================================ main
CSV_COLS = ["dataset", "symbol", "session", "tf", "proxy", "imb_ratio", "imb_bucket",
            "imb_colour", "n1_colour", "direction", "n1_high", "n1_low", "n1_range",
            "dist_N_to_n1_high", "dist_N_to_n1_low", "range_x", "vol_x", "pre_compression",
            "mtf_confirm", "n_mtf_confirm", "near_level_dist", "is_expiry_day",
            "oi_pcr", "oi_pcr_prev", "oi_d_ce", "oi_d_pe", "oi_atm_conc",
            "prem_CE_mfe", "prem_CE_mae", "prem_PE_mfe", "prem_PE_mae",
            "rr2_outcome", "rr2_realized_R", "rr3_outcome", "rr3_realized_R",
            "rr3_t_to_entry_s", "rr3_t_to_target_s", "rr3_t_to_sl_s", "rr3_MFE_R", "rr3_MAE_R",
            "rr4_outcome", "rr4_realized_R"]


def _flat(r):
    o = {k: r.get(k) for k in CSV_COLS}
    for rk in ("rr2", "rr3", "rr4"):
        w = r["rr_res"].get(rk) or {}
        for f in ("outcome", "realized_R", "t_to_entry_s", "t_to_target_s", "t_to_sl_s", "MFE_R", "MAE_R"):
            if f"{rk}_{f}" in CSV_COLS:
                o[f"{rk}_{f}"] = w.get(f)
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-kaggle", action="store_true")
    ap.add_argument("--csv", default=str(OUT_EVENTS))
    a = ap.parse_args()
    if not HDB.exists():
        sys.exit(f"[STOP] {HDB} not found")
    con = sqlite3.connect(f"file:{HDB}?mode=ro", uri=True)
    print("loading market_history futures 1m + book + option OI ...", file=sys.stderr)
    fut1m = load_fut_1m(con)
    book = load_book_series(con)
    opt_oi, opt_exp = load_option_oi(con)
    con.close()
    kaggle = {} if a.no_kaggle else load_kaggle_nifty_5m()
    print(f"  futures syms={list(fut1m)} book syms={list(book)} opt_oi syms={list(opt_oi)} "
          f"kaggle sessions={len(kaggle)}", file=sys.stderr)
    ev = build_events(fut1m, book, opt_oi, opt_exp, kaggle)
    print(f"  {len(ev)} proxy events", file=sys.stderr)
    report(ev, sys.stdout)
    with open(a.csv, "w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for r in ev:
            w.writerow(_flat(r))
    print(f"\nwrote {len(ev)} events -> {a.csv}")


if __name__ == "__main__":
    main()
