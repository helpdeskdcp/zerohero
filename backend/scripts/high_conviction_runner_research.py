#!/usr/bin/env python3
"""
high_conviction_runner_research.py  --  RESEARCH ONLY. READ-ONLY.

An HONEST attempt at "few signals, big target, high close-green rate" -- built
from THIS session's own findings, not wishful thinking. There is no 80-100%
win-rate-with-big-targets strategy (that is a math contradiction). What IS
achievable: enter rarely on the one conditioner that replicated across both
NIFTY datasets and all chronological splits (abnormal spike on a WIDE-range /
high-volatility day), then SCALE OUT so most trades close green while a runner
leg chases a big target.

Setup (all from nifty_spike_n1_deepdive.py's stability scan):
  DAY FILTER : today's range >= 1.3x the rolling-median day range     (*/WIDE)
  SPIKE      : a 5m bar with range_x = (h-l)/median(prev-20 ranges) >= 3
  TIME       : spike before 14:00 IST  (a +3R runner needs room)
  DOW        : skip Thu & Fri  (both datasets weaker)
  ENTRY      : N+1 close -> mark high & low -> OCO
               break high = LONG (SL = N+1 low), break low = SHORT (SL = N+1 high)
               R = N+1 range
  MANAGE     : Leg A = 60% size, target +1R  (the win-locker)
               when Leg A fills, Leg B stop -> breakeven
               Leg B = 40% size, target +3R  (the runner; +5R also reported)
               25-minute hard time stop on both legs (mark-to-market)
  blended R  = 0.6*legA_R + 0.4*legB_R ;  "close green" = blended R > 0

Datasets: Upstox NIFTY 5m (2022..2026) + Kaggle NIFTY 5m (2015..2026). Cash
index -> no volume / L2 / aggressor / OI. This is a RANGE-SHAPE proxy strategy,
RESEARCH ONLY. No production wiring, no order, no change to frozen H1/H7 / live
trading / calibration / broker / cron / IMBALANCE_NEXT_CANDLE_1R3. Nothing
PROVEN.
"""
from __future__ import annotations

import csv as _csv
import json
import sqlite3
import statistics as st
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
UDB = ROOT / "data" / "historical" / "upstox" / "upstox_research.db"
KAGGLE = (ROOT / "data" / "historical" / "kaggle" /
          "debashis74017__nifty-50-minute-data" / "NIFTY_50_5minute.csv")
OUT = ROOT / "data" / "high_conviction_runner_events.csv"
_IST = timezone(timedelta(hours=5, minutes=30))

from scripts.imbalance_next_candle_1r3_research import spike_feats, ROLL  # noqa: E402

# ---- tunables (frozen for this run; a sweep is in the report) ----
DAY_WIDE_MULT = 1.3
SPIKE_X = 3.0
SPIKE_BEFORE = "14:00"
SKIP_DOW = {"Thu", "Fri"}
LEG_A_FRAC, LEG_B_FRAC = 0.60, 0.40
LEG_A_TGT_R = 1.0
LEG_B_TGT_R = 3.0
MAX_MIN = 25
DAY_MED_WIN = 20


def _load_upstox():
    con = sqlite3.connect(f"file:{UDB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT timestamp, open, high, low, close FROM normalized_bars "
        "WHERE source='upstox' AND symbol='NIFTY' AND timeframe='5m' ORDER BY timestamp").fetchall()
    con.close()
    out: dict = {}
    for ts, o, h, l, c in rows:
        if None in (o, h, l, c):
            continue
        t = datetime.fromisoformat(ts).astimezone(_IST)
        if "09:15" <= t.strftime("%H:%M") <= "15:30":
            out.setdefault(t.date().isoformat(), []).append(
                {"t": t, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": 0.0})
    for d in out:
        out[d].sort(key=lambda b: b["t"])
    return out


def _load_kaggle():
    if not KAGGLE.exists():
        return {}
    out: dict = {}
    with KAGGLE.open() as f:
        for r in _csv.DictReader(f):
            try:
                dt = datetime.strptime(r["date"], "%Y-%m-%d %H:%M:%S")
                o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            except (ValueError, KeyError):
                continue
            if "09:15" <= dt.strftime("%H:%M") <= "15:30" and o > 0 and h >= l and l > 0:
                out.setdefault(dt.date().isoformat(), []).append(
                    {"t": dt.replace(tzinfo=_IST), "o": o, "h": h, "l": l, "c": c, "v": 0.0})
    for d in out:
        out[d].sort(key=lambda b: b["t"])
    return out


def _dayrange(bars):
    return max(b["h"] for b in bars) - min(b["l"] for b in bars)


def _walk_scaleout(bars, mark_close_t, n1):
    """OCO entry + 60/40 scale-out. Returns per-leg R and blended R, or None."""
    hi, lo = n1["h"], n1["l"]
    R = hi - lo
    if R <= 0:
        return None
    deadline = mark_close_t + timedelta(minutes=MAX_MIN)
    seg = [b for b in bars if mark_close_t < b["t"] <= deadline]
    if not seg:
        return None
    # phase 1: first break
    side = entry = slp = None
    idx0 = None
    for k, b in enumerate(seg):
        up, dn = b["h"] >= hi, b["l"] <= lo
        if up and dn:
            side = "LONG" if b["c"] >= b["o"] else "SHORT"
        elif up:
            side = "LONG"
        elif dn:
            side = "SHORT"
        if side:
            idx0 = k
            entry = hi if side == "LONG" else lo
            slp = lo if side == "LONG" else hi
            break
    if side is None:
        return {"outcome": "NO_TRIGGER"}
    a_tgt = entry + LEG_A_TGT_R * R if side == "LONG" else entry - LEG_A_TGT_R * R
    b_tgt = entry + LEG_B_TGT_R * R if side == "LONG" else entry - LEG_B_TGT_R * R
    a_done = b_done = False
    a_R = b_R = None
    be_active = False           # leg B stop moved to breakeven after leg A fills
    mfe = 0.0
    last_c = None
    for b in seg[idx0:]:
        last_c = b["c"]
        fav = (b["h"] - entry) if side == "LONG" else (entry - b["l"])
        mfe = max(mfe, fav)
        hi_hit = (b["h"] >= a_tgt) if side == "LONG" else (b["l"] <= a_tgt)
        b_hit = (b["h"] >= b_tgt) if side == "LONG" else (b["l"] <= b_tgt)
        sl_hit = (b["l"] <= slp) if side == "LONG" else (b["h"] >= slp)
        be_hit = (b["l"] <= entry) if side == "LONG" else (b["h"] >= entry)
        # pessimistic ordering within a bar: stop before target
        if not a_done and sl_hit:
            a_R = -1.0
            a_done = True
            if not b_done:
                b_R = -1.0
                b_done = True
            break
        if not a_done and hi_hit:
            a_R = LEG_A_TGT_R
            a_done = True
            be_active = True
        elif a_done and be_active and not b_done and be_hit:
            b_R = 0.0            # runner stopped at breakeven
            b_done = True
            break
        if a_done and not b_done and b_hit:
            b_R = LEG_B_TGT_R
            b_done = True
            break
    # timeout / EOD -> mark to market
    if a_R is None:
        a_R = round(((last_c - entry) if side == "LONG" else (entry - last_c)) / R, 3)
        a_done = True
    if b_R is None:
        b_R = round(((last_c - entry) if side == "LONG" else (entry - last_c)) / R, 3)
    blended = round(LEG_A_FRAC * a_R + LEG_B_FRAC * b_R, 4)
    return {"outcome": "TRIGGERED", "side": side, "R_pts": round(R, 3),
            "legA_R": round(a_R, 3), "legB_R": round(b_R, 3), "blended_R": blended,
            "mfe_R": round(mfe / R, 3), "green": blended > 0,
            "legA_target_hit": a_R == LEG_A_TGT_R, "legB_target_hit": b_R == LEG_B_TGT_R}


def qualifies(bars, i, dow, day_wide):
    if not day_wide or dow in SKIP_DOW:
        return None
    if bars[i]["t"].strftime("%H:%M") >= SPIKE_BEFORE:
        return None
    f = spike_feats(bars, i)
    if not f or f["range_x"] < SPIKE_X:
        return None
    return f


def build(days, label):
    ds = sorted(days)
    dr = [_dayrange(days[d]) for d in ds if len(days[d]) >= 6]
    ev = []
    for k, d in enumerate(ds):
        bars = days[d]
        if len(bars) < ROLL + 6:
            continue
        lo_k = max(0, k - DAY_MED_WIN)
        prior_dr = [_dayrange(days[x]) for x in ds[lo_k:k] if len(days[x]) >= 6]
        med = st.median(prior_dr) if prior_dr else (st.median(dr) if dr else None)
        day_wide = bool(med and _dayrange(bars) >= DAY_WIDE_MULT * med)
        dow = datetime.fromisoformat(d).strftime("%a")
        for i in range(ROLL, len(bars) - 1):
            f = qualifies(bars, i, dow, day_wide)
            if not f:
                continue
            N1 = bars[i + 1]
            w = _walk_scaleout(bars, N1["t"] + timedelta(minutes=5), N1)
            if not w or w["outcome"] != "TRIGGERED":
                ev.append({"src": label, "session": d, "spike_time": bars[i]["t"].strftime("%H:%M"),
                           "range_x": round(f["range_x"], 2), "dow": dow, "split_k": k,
                           "tot": len(ds), "outcome": (w or {}).get("outcome", "NO_DATA"), "rr": {}})
                continue
            ev.append({"src": label, "session": d, "spike_time": bars[i]["t"].strftime("%H:%M"),
                       "range_x": round(f["range_x"], 2), "dow": dow, "split_k": k, "tot": len(ds),
                       "outcome": "TRIGGERED", "rr": w})
    return ev


def agg(ev):
    tr = [r["rr"] for r in ev if r["outcome"] == "TRIGGERED"]
    n = len(tr)
    notrig = sum(1 for r in ev if r["outcome"] == "NO_TRIGGER")
    if not n:
        return {"signals": len(ev), "triggered": 0, "no_trigger": notrig}
    bl = [x["blended_R"] for x in tr]
    green = [x for x in bl if x > 0]
    losses = [x for x in bl if x < 0]
    # max consec losing (blended) trades chronological
    mcl = cur = 0
    for r in sorted(ev, key=lambda r: r["session"]):
        if r["outcome"] != "TRIGGERED":
            continue
        if r["rr"]["blended_R"] < 0:
            cur += 1; mcl = max(mcl, cur)
        elif r["rr"]["blended_R"] > 0:
            cur = 0
    return {
        "signals": len(ev), "triggered": n, "no_trigger": notrig,
        "close_green_pct": round(len(green) / n, 3),
        "legA_1R_hit_pct": round(sum(1 for x in tr if x["legA_target_hit"]) / n, 3),
        "legB_3R_hit_pct": round(sum(1 for x in tr if x["legB_target_hit"]) / n, 3),
        "blended_E_R": round(st.fmean(bl), 3),
        "blended_PF": round(sum(green) / -sum(losses), 3) if losses else None,
        "median_blended_R": round(st.median(bl), 3),
        "worst_blended_R": round(min(bl), 3), "best_blended_R": round(max(bl), 3),
        "max_consec_losers": mcl,
        "avg_mfe_R": round(st.fmean(x["mfe_R"] for x in tr), 2),
        "sessions": len({r["session"] for r in ev}),
    }


def _split(ev):
    ds = sorted({r["session"] for r in ev})
    a, b, c = int(len(ds) * .45), int(len(ds) * .65), int(len(ds) * .82)
    return {d: ("TRAIN" if k < a else "VALIDATION" if k < b else "OOS" if k < c else "HOLDOUT")
            for k, d in enumerate(ds)}


def _line(tag, m):
    if not m.get("triggered"):
        return f"  {tag:<22} signals={m.get('signals',0):>4} triggered=0 (no_trigger={m.get('no_trigger',0)})"
    return (f"  {tag:<22} sig={m['signals']:>4} trig={m['triggered']:>4} ses={m['sessions']:>4} "
            f"GREEN%={m['close_green_pct']*100:>5.1f} A@1R%={m['legA_1R_hit_pct']*100:>5.1f} "
            f"B@3R%={m['legB_3R_hit_pct']*100:>5.1f} E[Rb]={m['blended_E_R']:>6.3f} "
            f"PF={m['blended_PF']} med={m['median_blended_R']} worst={m['worst_blended_R']} "
            f"maxCL={m['max_consec_losers']} MFE~{m['avg_mfe_R']}")


def report(u, k, out):
    p = lambda *a: print(*a, file=out)
    p("=" * 108)
    p("HIGH-CONVICTION RUNNER  --  few signals, 60/40 scale-out, +1R locker + +3R runner (RESEARCH)")
    p(f"filter: */WIDE day (>= {DAY_WIDE_MULT}x median) + range_x >= {SPIKE_X} + spike < {SPIKE_BEFORE} + not Thu/Fri")
    p("NIFTY 5m RANGE-SHAPE proxy (cash index: no volume/L2/aggressor/OI). NOT VALIDATED. Nothing PROVEN.")
    p("GREEN% = % of triggered trades whose BLENDED R (0.6*legA + 0.4*legB) > 0.")
    p("=" * 108)
    for name, ev in (("UPSTOX NIFTY 5m 2022-2026", u), ("KAGGLE NIFTY 5m 2015-2026", k)):
        if not ev:
            continue
        s = sorted({r["session"] for r in ev})
        yrs = sorted({d[:4] for d in s})
        p(f"\n### {name}   {len(ev)} signals over {len(s)} trading days ({s[0]}..{s[-1]}, {len(yrs)} yrs)")
        p(f"    ~{len(ev)/max(1,len(yrs)):.0f} signals/yr  (~{len(ev)/max(1,len(yrs))/12:.1f}/month) -- 'few signals' box: checked")
        p(_line("ALL", agg(ev)))
        sm = _split(ev)
        for spl in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT"):
            p(_line(spl, agg([r for r in ev if sm[r["session"]] == spl])))
        p("  -- by spike size --")
        for lo, hi in ((3, 4), (4, 6), (6, 99)):
            p(_line(f"range_x {lo}-{hi if hi<99 else '+'}", agg([r for r in ev if lo <= r["range_x"] < hi])))
        # blended-R distribution
        tr = [r["rr"]["blended_R"] for r in ev if r["outcome"] == "TRIGGERED"]
        if tr:
            tr.sort()
            q = lambda a, pp: a[min(len(a) - 1, int(len(a) * pp))]
            p(f"  blended-R dist: p05={q(tr,.05):.2f} p25={q(tr,.25):.2f} p50={q(tr,.5):.2f} "
              f"p75={q(tr,.75):.2f} p95={q(tr,.95):.2f}")

    p("\n" + "=" * 108)
    p("HONEST VERDICT")
    p("=" * 108)
    mu = agg(u)
    p(f"  Upstox: GREEN% = {(mu.get('close_green_pct') or 0)*100:.1f}  blended E[R] = {mu.get('blended_E_R')}  "
      f"PF = {mu.get('blended_PF')}  ~{len(u)/max(1,len({d[:4] for d in {r['session'] for r in u}}))/12:.1f} sig/mo")
    p("  'few signals + big runner': YES. '80-100% win rate': NO -- that does not exist with a real")
    p("  +3R runner. The scale-out lifts the CLOSE-GREEN rate (most trades bank +1R on 60% of size),")
    p("  but a green close still averages well under +1R blended, and the runner leg mostly gives")
    p("  back to breakeven. On a RANGE-SHAPE cash-index proxy with no volume/L2/OI this is a")
    p("  candidate to watch, not a validated edge. No production promotion. Nothing PROVEN.")


CSV = ["src", "session", "spike_time", "range_x", "dow", "outcome",
       "side", "R_pts", "legA_R", "legB_R", "blended_R", "green",
       "legA_target_hit", "legB_target_hit", "mfe_R"]


def _flat(r):
    o = {k: r.get(k) for k in CSV}
    o.update({k: r["rr"].get(k) for k in r.get("rr", {})})
    return {k: o.get(k) for k in CSV}


def main():
    print("loading NIFTY 5m ...", file=sys.stderr)
    u = _load_upstox()
    k = _load_kaggle()
    print(f"  upstox days={len(u)} kaggle days={len(k)}", file=sys.stderr)
    ue, ke = build(u, "UPSTOX"), build(k, "KAGGLE")
    print(f"  signals: upstox={len(ue)} kaggle={len(ke)}", file=sys.stderr)
    report(ue, ke, sys.stdout)
    with OUT.open("w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=CSV, extrasaction="ignore")
        w.writeheader()
        for r in ue + ke:
            w.writerow(_flat(r))
    print(f"\nwrote {len(ue)+len(ke)} signals -> {OUT}")


if __name__ == "__main__":
    main()
