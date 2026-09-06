#!/usr/bin/env python3
"""
orderflow_spike_ledger.py -- RESEARCH ONLY. Descriptive per-spike event log.

The operator's per-spike diagnostic chain (2026-09-06):
  ABNORMAL SPIKE
   -> at which price level?
   -> near which option strike?
   -> where in the index profile?
   -> how abnormal is volume?
   -> what did OI change?
   -> did the spike break a level?
   -> after the break: acceptance or rejection?
   -> what does the next candle do?
   -> does structure form?
   -> where would entry have been?
   -> where is the logical SL from that entry?
   -> from that SL, how many R is the MFE?
   -> how far (in R) is the next major S/R?

This is a LEDGER, not a backtest: one row per abnormal-spike event with every
field above, purely descriptive -- no expectancy, no profit factor, no
verdict, no threshold tuning. It exists so that as sessions accumulate we
have rich per-event data to study later. Honours the "wait for more sessions"
hold: no ablation is re-run and no edge is claimed.

Causal: completed candles only, developing volume profile from bars[:idx],
OI deltas from quote_snapshots at/-5min around the spike bar. Reuses the
helpers in orderflow_sequence_research.py.

Usage:  python backend/scripts/orderflow_spike_ledger.py [--symbols NIFTY,...]
        [--min-rel-range 1.8] [--min-vol-x 1.8] [--csv path]
A bar is logged if EITHER rel_range >= min-rel-range OR vol_x >= min-vol-x
(permissive on purpose -- every metric is recorded so any stricter spike
definition can be applied to the CSV afterwards).
"""
from __future__ import annotations

import argparse
import csv as _csv
import sqlite3
import statistics as st
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import market_hub
from scripts.orderflow_sequence_research import (
    Sess, _prof_loc, _rejection, _regime,
)

_HDB = str(Path(__file__).resolve().parents[1] / "data" / "market_history.db")


def _ro():
    c = sqlite3.connect(f"file:{_HDB}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


# --------------------------------------------------------------- option context
def _dt(x):
    return datetime.fromisoformat(x.replace("Z", "+00:00"))


def _load_opt_ctx(sym, sess):
    """{(strike, CE/PE): [(ts, ltp, oi), ...]} for the session, oldest-first."""
    out = {}
    with _ro() as c:
        for r in c.execute(
            "SELECT received_ts, strike, option_type, ltp, oi FROM quote_snapshots "
            "WHERE symbol=? AND kind='OPTION' AND session_date_ist=? AND strike IS NOT NULL "
            "ORDER BY received_ts", (sym, sess)):
            out.setdefault((float(r["strike"]), r["option_type"]), []).append(
                (r["received_ts"], r["ltp"], r["oi"]))
    return out


def _asof(series, ts, col):
    lo, hi = 0, len(series)
    while lo < hi:
        m = (lo + hi) // 2
        if series[m][0] <= ts:
            lo = m + 1
        else:
            hi = m
    return series[lo - 1][col] if lo > 0 else None


def _opt_at(opt_ctx, ref_price, side, bar_ts):
    """Nearest captured strike to ref_price for the trade's option; its LTP and
    OI at bar_ts and OI ~5 min earlier -> delta."""
    ot = "CE" if side == "BUY" else "PE"
    strikes = sorted({k[0] for k in opt_ctx if k[1] == ot and opt_ctx[k]})
    if not strikes:
        return {"strike": None, "opt_ltp": None, "oi": None, "oi_5m_ago": None, "oi_delta": None}
    k = (min(strikes, key=lambda s: abs(s - ref_price)), ot)
    ser = opt_ctx[k]
    ltp = _asof(ser, bar_ts, 1)
    oi_now = _asof(ser, bar_ts, 2)
    prev_ts = (_dt(bar_ts) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    oi_prev = _asof(ser, prev_ts, 2)
    delta = (oi_now - oi_prev) if (oi_now is not None and oi_prev is not None) else None
    return {"strike": k[0], "opt_ltp": ltp, "oi": oi_now, "oi_5m_ago": oi_prev,
            "oi_delta": delta}


# --------------------------------------------------------------- structure bits
def _prior_levels(s: Sess, idx: int):
    """Causal structural levels visible before bar idx: developing VAH/VAL/POC,
    nearest prior fractal high and low."""
    va = s.va[idx]
    fr_hi = next((s.clean[j]["h"] for j in range(idx - 2, 0, -1) if s.frac_hi[j]), None)
    fr_lo = next((s.clean[j]["l"] for j in range(idx - 2, 0, -1) if s.frac_lo[j]), None)
    return {"vah": va[2] if va else None, "val": va[0] if va else None,
            "poc": va[1] if va else None, "prior_frac_hi": fr_hi, "prior_frac_lo": fr_lo}


def _level_break(s: Sess, idx: int, side: str, lv: dict):
    """Did the spike bar take out a prior level in the trade direction?"""
    b = s.clean[idx]
    cands = []
    if side == "BUY":
        for name in ("vah", "prior_frac_hi", "poc"):
            if lv.get(name) is not None and b["h"] > lv[name] >= b["o"] if b.get("o") else b["h"] > lv[name]:
                cands.append((name, lv[name], round(b["h"] - lv[name], 2)))
    else:
        for name in ("val", "prior_frac_lo", "poc"):
            if lv.get(name) is not None and b["l"] < lv[name]:
                cands.append((name, lv[name], round(lv[name] - b["l"], 2)))
    if not cands:
        return {"broke": False, "level": None, "level_price": None, "beyond_pts": None}
    name, price, beyond = max(cands, key=lambda x: x[2])
    return {"broke": True, "level": name, "level_price": round(price, 2), "beyond_pts": beyond}


def _acceptance(s: Sess, idx: int, side: str, lv_price):
    """After a break: acceptance (close held beyond + next bar continues) vs
    rejection (wick back inside / next bar reverses)."""
    if lv_price is None:
        return "NA"
    b = s.clean[idx]
    c = s.clean
    held = (b["c"] > lv_price) if side == "BUY" else (b["c"] < lv_price)
    if idx + 1 < len(c):
        nb = c[idx + 1]
        cont = (nb["c"] >= b["c"]) if side == "BUY" else (nb["c"] <= b["c"])
        back_in = (nb["c"] < lv_price) if side == "BUY" else (nb["c"] > lv_price)
    else:
        cont = held
        back_in = not held
    if held and cont:
        return "ACCEPTANCE"
    if (not held) or back_in:
        return "REJECTION"
    return "MIXED"


def _next_candle(s: Sess, idx: int, side: str):
    if idx + 1 >= len(s.clean):
        return {"dir": None, "body_x": None, "continued": None, "new_extreme": None}
    b, nb = s.clean[idx], s.clean[idx + 1]
    rng = nb["h"] - nb["l"]
    body = abs((nb.get("c") or 0) - (nb.get("o") or 0))
    up = (nb.get("c") or 0) >= (nb.get("o") or 0)
    cont = (nb["c"] >= b["c"]) if side == "BUY" else (nb["c"] <= b["c"])
    newx = (nb["h"] > b["h"]) if side == "BUY" else (nb["l"] < b["l"])
    return {"dir": "UP" if up else "DOWN",
            "body_x": round(body / rng, 2) if rng > 0 else None,
            "continued": bool(cont), "new_extreme": bool(newx)}


def _structure(s: Sess, idx: int, side: str, window: int = 3):
    """Does a confirmed higher-low (BUY) / lower-high (SELL) form within
    `window` bars after the spike?"""
    c = s.clean
    end = min(len(c) - 1, idx + window)
    for j in range(idx + 1, end):
        if side == "BUY" and s.frac_lo[j] and c[j]["l"] > c[idx]["l"]:
            return {"formed": True, "bar_offset": j - idx, "price": round(c[j]["l"], 2)}
        if side == "SELL" and s.frac_hi[j] and c[j]["h"] < c[idx]["h"]:
            return {"formed": True, "bar_offset": j - idx, "price": round(c[j]["h"], 2)}
    return {"formed": False, "bar_offset": None, "price": None}


def _hypo_entry_sl(s: Sess, idx: int, side: str):
    """Two entry hypotheses (S0 spike close / S2 rejection-candle close within
    2 bars) with a structural SL each."""
    c = s.clean
    out = {}
    # S0
    if idx + 1 < len(c):
        e0 = c[idx]["c"]
        sl0 = c[idx]["l"] if side == "BUY" else c[idx]["h"]
        out["S0"] = {"entry": round(e0, 2), "sl": round(sl0, 2),
                     "R": round(abs(e0 - sl0), 2), "ebar": idx + 1}
    # S2
    rej = next((j for j in range(idx, min(len(c), idx + 3)) if _rejection(c[j], side)), None)
    if rej is not None and rej + 1 < len(c):
        e2 = c[rej]["c"]
        sl2 = c[rej]["l"] if side == "BUY" else c[rej]["h"]
        out["S2"] = {"entry": round(e2, 2), "sl": round(sl2, 2),
                     "R": round(abs(e2 - sl2), 2), "ebar": rej + 1}
    return out


def _mfe_R(s: Sess, ebar: int, entry: float, sl: float, side: str):
    """MFE in R that is actually reachable BEFORE the logical SL takes the
    trade out (MFE accumulation stops at the SL-hit bar). Also returns the
    to-session-end MFE for reference and the SL-hit bar offset."""
    R = abs(entry - sl)
    if R <= 0:
        return None, None, None
    mfe = mfe_eod = 0.0
    stopped_at = None
    for i in range(ebar, len(s.clean)):
        b = s.clean[i]
        fav = (b["h"] - entry) if side == "BUY" else (entry - b["l"])
        mfe_eod = max(mfe_eod, fav)
        if stopped_at is None:
            mfe = max(mfe, fav)
            if (b["l"] <= sl) if side == "BUY" else (b["h"] >= sl):
                stopped_at = i - ebar
    return round(mfe / R, 2), stopped_at, round(mfe_eod / R, 2)


def _next_sr_R(s: Sess, idx: int, side: str, entry: float, R: float, lv: dict):
    """Distance from entry to the next major S/R on the far side, in R."""
    if not R:
        return None
    targets = []
    if side == "BUY":
        for v in (lv.get("vah"), lv.get("prior_frac_hi")):
            if v is not None and v > entry:
                targets.append(v)
        # a fractal high AFTER the spike that price hasn't reached yet
    else:
        for v in (lv.get("val"), lv.get("prior_frac_lo")):
            if v is not None and v < entry:
                targets.append(v)
    if not targets:
        return None
    nearest = min(targets, key=lambda v: abs(v - entry))
    return round(abs(nearest - entry) / R, 2)


# --------------------------------------------------------------- ledger
FIELDS = ["symbol", "session", "regime", "bar_ts", "side",
          "price_close", "price_high", "price_low",
          "nearest_strike", "opt_ltp", "oi", "oi_delta_5m",
          "profile_loc", "dist_poc", "dist_vah", "dist_val",
          "vol_x", "range_x", "range_pctile",
          "broke_level", "level_name", "level_price", "beyond_pts",
          "post_break", "next_dir", "next_body_x", "next_continued", "next_new_extreme",
          "structure_formed", "structure_bar_offset",
          "entry_model", "entry", "sl", "R_pts", "mfe_R", "mfe_R_eod",
          "sl_hit_bars", "next_SR_R"]


def build_ledger(symbols, min_rel, min_volx):
    rows = []
    for sym in symbols:
        dates = sorted(market_hub.session_dates(sym, limit=400))
        for d in dates:
            s = Sess(sym, d)
            if len(s.clean) < 12:
                continue
            opt_ctx = _load_opt_ctx(sym, d)
            if not opt_ctx:
                continue
            for idx in range(3, len(s.clean) - 1):
                if s.base[idx] <= 0:
                    continue
                b = s.clean[idx]
                rng = b["h"] - b["l"]
                rel = rng / s.base[idx]
                vx = (b["v"] / s.avgvol[idx]) if s.avgvol[idx] > 0 and b["v"] else None
                if rel < min_rel and not (vx and vx >= min_volx):
                    continue
                prior = [x["h"] - x["l"] for x in s.clean[:idx] if x["h"] > x["l"]]
                pctile = round(sum(1 for x in prior if x <= rng) / len(prior), 3) if len(prior) >= 10 else None
                lv = _prior_levels(s, idx)
                for side in ("BUY", "SELL"):
                    oc = _opt_at(opt_ctx, b["c"], side, b["bar_start"])
                    loc, dpoc, dvah, dval = _prof_loc(s, idx, b["c"])
                    lb = _level_break(s, idx, side, lv)
                    acc = _acceptance(s, idx, side, lb["level_price"])
                    nc = _next_candle(s, idx, side)
                    stc = _structure(s, idx, side)
                    hyp = _hypo_entry_sl(s, idx, side)
                    for em, h in hyp.items():
                        mfe_r, slhit, mfe_eod_r = _mfe_R(s, h["ebar"], h["entry"], h["sl"], side)
                        sr = _next_sr_R(s, idx, side, h["entry"], h["R"], lv)
                        rows.append({
                            "symbol": sym, "session": d, "regime": s.regime,
                            "bar_ts": b["bar_start"], "side": side,
                            "price_close": round(b["c"], 2), "price_high": round(b["h"], 2),
                            "price_low": round(b["l"], 2),
                            "nearest_strike": oc["strike"], "opt_ltp": oc["opt_ltp"],
                            "oi": oc["oi"], "oi_delta_5m": oc["oi_delta"],
                            "profile_loc": loc, "dist_poc": dpoc, "dist_vah": dvah, "dist_val": dval,
                            "vol_x": round(vx, 2) if vx else None,
                            "range_x": round(rel, 2), "range_pctile": pctile,
                            "broke_level": lb["broke"], "level_name": lb["level"],
                            "level_price": lb["level_price"], "beyond_pts": lb["beyond_pts"],
                            "post_break": acc, "next_dir": nc["dir"], "next_body_x": nc["body_x"],
                            "next_continued": nc["continued"], "next_new_extreme": nc["new_extreme"],
                            "structure_formed": stc["formed"], "structure_bar_offset": stc["bar_offset"],
                            "entry_model": em, "entry": h["entry"], "sl": h["sl"],
                            "R_pts": h["R"], "mfe_R": mfe_r, "mfe_R_eod": mfe_eod_r,
                            "sl_hit_bars": slhit, "next_SR_R": sr,
                        })
    return rows


def _summary(rows):
    if not rows:
        print("  (no spike events)")
        return
    by_sym = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    for sym, rs in by_sym.items():
        # dedupe events (one bar -> up to 2 sides x 2 entry models); count bar-events
        ev = {(r["session"], r["bar_ts"]) for r in rs}
        broke = [r for r in rs if r["broke_level"]]
        acc = [r for r in broke if r["post_break"] == "ACCEPTANCE"]
        rej = [r for r in broke if r["post_break"] == "REJECTION"]
        strf = [r for r in rs if r["structure_formed"]]
        mfes = [r["mfe_R"] for r in rs if r["mfe_R"] is not None]
        srs = [r["next_SR_R"] for r in rs if r["next_SR_R"] is not None]
        oid = [r["oi_delta_5m"] for r in rs if r["oi_delta_5m"] is not None]
        sl_quick = [r for r in rs if r["sl_hit_bars"] is not None and r["sl_hit_bars"] <= 2]
        sl_before1 = [r for r in rs if r["sl_hit_bars"] is not None and (r["mfe_R"] or 0) < 1.0]
        print(f"\n  {sym}: {len(ev)} spike bar-events, {len(rs)} (side x entry-model) rows, "
              f"{len({r['session'] for r in rs})} sessions, regimes {sorted({r['regime'] for r in rs})}")
        print(f"    broke a prior level: {len(broke)} rows  "
              f"(acceptance {len(acc)} / rejection {len(rej)} / mixed-NA {len(broke)-len(acc)-len(rej)})")
        print(f"    structure formed within 3 bars: {len(strf)}/{len(rs)}")
        print(f"    logical-SL hit within 2 bars: {len(sl_quick)}/{len(rs)} "
              f"({100*len(sl_quick)/len(rs):.0f}%)  |  SL hit before +1R: "
              f"{len(sl_before1)}/{len(rs)} ({100*len(sl_before1)/len(rs):.0f}%)")
        print(f"    OI 5m-delta available: {len(oid)}/{len(rs)}  "
              f"median {round(st.median(oid),0) if oid else None}")
        if mfes:
            mfes.sort()
            print(f"    MFE in R (capped at the logical SL): median {round(st.median(mfes),2)}  "
                  f"p25 {mfes[len(mfes)//4]}  p75 {mfes[3*len(mfes)//4]}  "
                  f">=1R {round(sum(1 for x in mfes if x>=1)/len(mfes)*100)}%  "
                  f">=2R {round(sum(1 for x in mfes if x>=2)/len(mfes)*100)}%  "
                  f">=3R {round(sum(1 for x in mfes if x>=3)/len(mfes)*100)}%")
        if srs:
            srs.sort()
            print(f"    next major S/R distance in R: median {round(st.median(srs),2)}  "
                  f"p25 {srs[len(srs)//4]}  p75 {srs[3*len(srs)//4]}")
        # profile-location breakdown of MFE
        loc_mfe = {}
        for r in rs:
            if r["mfe_R"] is not None:
                loc_mfe.setdefault(r["profile_loc"], []).append(r["mfe_R"])
        print("    MFE(R) by profile location:  " + "  ".join(
            f"{k}:{round(st.median(v),2)}(n{len(v)})" for k, v in sorted(loc_mfe.items())))
    print("\n  DESCRIPTIVE ONLY -- no expectancy / PF / verdict. 4 sessions; "
          "accumulate to >=10 before drawing operational conclusions.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,NATURALGAS,CRUDEOIL")
    ap.add_argument("--min-rel-range", type=float, default=1.8)
    ap.add_argument("--min-vol-x", type=float, default=1.8)
    ap.add_argument("--csv", default="data/orderflow_spike_ledger.csv")
    a = ap.parse_args()
    syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    rows = build_ledger(syms, a.min_rel_range, a.min_vol_x)
    outp = Path(a.csv)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with open(outp, "w", newline="") as f:
        wr = _csv.DictWriter(f, fieldnames=FIELDS)
        wr.writeheader()
        wr.writerows(rows)
    print(f"wrote {len(rows)} rows -> {outp}")
    _summary(rows)


if __name__ == "__main__":
    main()
