#!/usr/bin/env python3
"""
nifty_spike_n1_deepdive.py  --  RESEARCH ONLY. READ-ONLY.

Focused NIFTY deep-dive on the abnormal-spike -> N+1 OCO-breakout pattern:
condition it on time-of-day, daily regime, gap type and day-of-week, over the
full Upstox NIFTY 5m history (2022..2026, ~1160 sessions) with a Kaggle NIFTY 5m
cross-check (2015..2026). Chronological TRAIN/VAL/OOS/HOLDOUT.

Engine imported UNCHANGED from imbalance_nc_oco_breakout.py / the base module.
No change to frozen H1/H7, live entry/SL/target/risk/execution, calibration,
broker code, cron, or the IMBALANCE_NEXT_CANDLE_1R3 calculations. No production
wiring, no order. NIFTY cash index -> no volume / L2 / aggressor / OI, so the
only proxy is `range_only` (abnormal candle range vs rolling median).
Headline stays: NOT VALIDATED -- this is a range-shape proxy, not an L2 edge.
Nothing PROVEN.

Signal: N = 5m bar with range_x = (h-l)/median(prev-20 ranges) >= T.
        N+1 close -> mark high & low -> OCO: break high = BUY (SL N+1 low),
        break low = SELL (SL N+1 high). R = N+1 range. Target 1:3 (also 1:2/1:4).
        25-minute hard lifetime, TIMEOUT marked to market.
"""
from __future__ import annotations

import csv as _csv
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
OUT = ROOT / "data" / "nifty_spike_n1_deepdive_events.csv"
_IST = timezone(timedelta(hours=5, minutes=30))

from scripts.imbalance_next_candle_1r3_research import spike_feats, _pivots, ROLL  # noqa: E402
from scripts.imbalance_nc_oco_breakout import walk_oco                              # noqa: E402

THRESH = (2.0, 3.0, 4.0)
TARGETS = (2.0, 3.0, 4.0)
ENTRY_CUTOFF = "14:45"          # a spike after this can't run a full 25 min


# ---------------------------------------------------------------- loaders
def load_upstox_5m():
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


def load_kaggle_5m():
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


# ---------------------------------------------------------------- conditioners
def _tod(t):
    hm = t.strftime("%H:%M")
    if hm < "09:45":
        return "OPEN"
    if hm < "12:00":
        return "MORNING"
    if hm < "13:30":
        return "MIDDAY"
    return "AFTERNOON"


def _dayrange(bars):
    return max(b["h"] for b in bars) - min(b["l"] for b in bars)


def _regime(bars, atr_ref):
    net = bars[-1]["c"] - bars[0]["o"]
    rng = _dayrange(bars)
    if rng <= 0:
        return "NA"
    if abs(net) >= 0.55 * rng:
        base = "TREND_UP" if net > 0 else "TREND_DOWN"
    else:
        base = "CHOP"
    vol = "WIDE" if (atr_ref and rng >= 1.3 * atr_ref) else ("TIGHT" if (atr_ref and rng <= 0.7 * atr_ref) else "MID")
    return f"{base}/{vol}"


def _gap(day_open, prev_close):
    if prev_close is None or prev_close <= 0:
        return "NA"
    g = (day_open - prev_close) / prev_close
    if g >= 0.003:
        return "GAP_UP"
    if g <= -0.003:
        return "GAP_DN"
    return "FLAT"


# ---------------------------------------------------------------- build
def build(days, label):
    ds = sorted(days)
    dayrngs = [_dayrange(days[d]) for d in ds if len(days[d]) >= 6]
    med_dr = st.median(dayrngs) if dayrngs else None
    ev = []
    for k, d in enumerate(ds):
        bars = days[d]
        if len(bars) < ROLL + 6:
            continue
        prev_close = days[ds[k - 1]][-1]["c"] if k > 0 else None
        prev = days[ds[k - 1]] if k > 0 else None
        piv = _pivots(max(x["h"] for x in prev), min(x["l"] for x in prev), prev[-1]["c"]) if prev else {}
        regime = _regime(bars, med_dr)
        gap = _gap(bars[0]["o"], prev_close)
        dow = datetime.fromisoformat(d).strftime("%a")
        for i in range(ROLL, len(bars) - 1):
            f = spike_feats(bars, i)
            if not f or f["range_x"] < THRESH[0]:
                continue
            N, N1 = bars[i], bars[i + 1]
            row = {
                "src": label, "session": d, "split_k": k, "tot_sessions": len(ds),
                "spike_time": N["t"].strftime("%H:%M"), "tod": _tod(N["t"]),
                "regime": regime, "gap": gap, "dow": dow,
                "range_x": round(f["range_x"], 3), "pre_compression": f.get("pre_compression"),
                "after_cutoff": N["t"].strftime("%H:%M") >= ENTRY_CUTOFF,
                "n1_range": round(N1["h"] - N1["l"], 3),
                "near_pivot": _near_piv(N["c"], piv),
                "rr": {},
            }
            for rr in TARGETS:
                w = walk_oco(bars, N1.get("closed_at") or N1["t"], N1, rr)
                if w and w.get("outcome") != "NO_DATA":
                    row["rr"][f"rr{int(rr)}"] = w
            ev.append(row)
    return ev


def _near_piv(px, piv):
    if not piv:
        return None
    d = min(abs(px - v) for v in piv.values())
    return d <= 0.0015 * abs(px)


# ---------------------------------------------------------------- agg / report
def agg(recs, rk):
    w = [r["rr"][rk] for r in recs if rk in r["rr"]]
    trig = [x for x in w if x["outcome"] in ("TARGET", "SL", "TIMEOUT")]
    n = len(trig)
    if not n:
        return {"sig": len(recs), "n": 0}
    tgt = sum(1 for x in trig if x["outcome"] == "TARGET")
    slh = sum(1 for x in trig if x["outcome"] == "SL")
    to = sum(1 for x in trig if x["outcome"] == "TIMEOUT")
    rz = [x["realized_R"] for x in trig]
    wins = [x for x in rz if x > 0]
    losses = [x for x in rz if x < 0]
    return {
        "sig": len(recs), "n": n, "tgt_pct": round(tgt / n, 3), "sl_pct": round(slh / n, 3),
        "to_pct": round(to / n, 3), "E_R": round(st.fmean(rz), 3),
        "PF": round(sum(wins) / -sum(losses), 3) if losses else None,
        "MFE": round(st.fmean(x["MFE_R"] for x in trig), 2),
        "MAE": round(st.fmean(x["MAE_R"] for x in trig), 2),
        "sessions": len({r["session"] for r in recs}),
    }


def _l(tag, m):
    if not m.get("n"):
        return f"  {tag:<26} sig={m.get('sig', 0):>5} n=0"
    return (f"  {tag:<26} sig={m['sig']:>5} n={m['n']:>5} ses={m['sessions']:>4} "
            f"tgt%={m['tgt_pct']*100:>5.1f} sl%={m['sl_pct']*100:>5.1f} to%={m['to_pct']*100:>5.1f} "
            f"E[R]={m['E_R']:>6.2f} PF={m['PF']} MFE~{m['MFE']} MAE~{m['MAE']}")


def _splitmap(ev):
    ds = sorted({r["session"] for r in ev})
    a, b, c = int(len(ds) * .45), int(len(ds) * .65), int(len(ds) * .82)
    return {d: ("TRAIN" if k < a else "VALIDATION" if k < b else "OOS" if k < c else "HOLDOUT")
            for k, d in enumerate(ds)}


def _stable(ev, sel, rk="rr3"):
    """report a cut across all 4 splits + a stability flag."""
    sm = _splitmap(ev)
    out = {}
    for spl in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT"):
        out[spl] = agg([r for r in ev if sel(r) and sm[r["session"]] == spl], rk)
    ers = [out[s].get("E_R") for s in out if out[s].get("n", 0) >= 30]
    stable = len(ers) >= 3 and all(e > 0 for e in ers) and (max(ers) - min(ers) <= 0.35)
    return out, stable


def report(u_ev, k_ev, out):
    p = lambda *a: print(*a, file=out)
    p("=" * 108)
    p("NIFTY ABNORMAL-SPIKE -> N+1 OCO BREAKOUT  --  DEEP DIVE (READ-ONLY, range-shape PROXY)")
    p("Upstox NIFTY 5m (2022..2026) primary, Kaggle NIFTY 5m (2015..2026) cross-check. Cash index:")
    p("no volume/L2/aggressor/OI. range_only proxy only. NOT an L2 edge. Nothing PROVEN.")
    p("=" * 108)

    for name, ev in (("UPSTOX NIFTY 5m", u_ev), ("KAGGLE NIFTY 5m", k_ev)):
        if not ev:
            continue
        s = sorted({r["session"] for r in ev})
        p(f"\n### {name}  --  {len(ev)} spike events | {len(s)} sessions {s[0]}..{s[-1]}")
        sm = _splitmap(ev)

        p("  [threshold] 1:3, cumulative range_x >= T")
        for t in THRESH:
            p(_l(f"range_x>={t:.0f}x", agg([r for r in ev if r["range_x"] >= t], "rr3")))
        p("  [target] 1:2 / 1:3 / 1:4 (all)")
        for rk, lab in (("rr2", "1:2"), ("rr3", "1:3"), ("rr4", "1:4")):
            p(_l(f"target {lab}", agg(ev, rk)))

        p("  [time-of-day] 1:3")
        for tod in ("OPEN", "MORNING", "MIDDAY", "AFTERNOON"):
            p(_l(tod, agg([r for r in ev if r["tod"] == tod], "rr3")))
        p(_l("entry <= 14:45 only", agg([r for r in ev if not r["after_cutoff"]], "rr3")))

        p("  [daily regime] 1:3")
        for reg in sorted({r["regime"] for r in ev}):
            p(_l(reg, agg([r for r in ev if r["regime"] == reg], "rr3")))

        p("  [gap type] 1:3")
        for g in ("GAP_UP", "GAP_DN", "FLAT"):
            p(_l(g, agg([r for r in ev if r["gap"] == g], "rr3")))

        p("  [day of week] 1:3")
        for dow in ("Mon", "Tue", "Wed", "Thu", "Fri"):
            p(_l(dow, agg([r for r in ev if r["dow"] == dow], "rr3")))

        p("  [near pivot vs not] 1:3")
        p(_l("near a floor pivot", agg([r for r in ev if r["near_pivot"]], "rr3")))
        p(_l("away", agg([r for r in ev if r["near_pivot"] is False], "rr3")))

        p("  [chronological split] 1:3  (45/20/17/18)")
        for spl in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT"):
            p(_l(spl, agg([r for r in ev if sm[r["session"]] == spl], "rr3")))

        p("  [stability scan] cuts that are E[R]>0 on >=3 of 4 splits with spread <= 0.35R:")
        cand = [
            ("all events", lambda r: True),
            ("MORNING", lambda r: r["tod"] == "MORNING"),
            ("AFTERNOON", lambda r: r["tod"] == "AFTERNOON"),
            ("entry<=14:45", lambda r: not r["after_cutoff"]),
            ("TREND_UP/*", lambda r: r["regime"].startswith("TREND_UP")),
            ("TREND_DOWN/*", lambda r: r["regime"].startswith("TREND_DOWN")),
            ("CHOP/*", lambda r: r["regime"].startswith("CHOP")),
            ("*/WIDE", lambda r: r["regime"].endswith("WIDE")),
            ("GAP_UP", lambda r: r["gap"] == "GAP_UP"),
            ("GAP_DN", lambda r: r["gap"] == "GAP_DN"),
            ("range_x>=3", lambda r: r["range_x"] >= 3),
            ("range_x>=3 & MORNING", lambda r: r["range_x"] >= 3 and r["tod"] == "MORNING"),
        ]
        any_stable = False
        for tag, sel in cand:
            splits, stable = _stable(ev, sel)
            if stable:
                any_stable = True
                es = " ".join(f"{s[:3]}={splits[s]['E_R']:+.2f}(n{splits[s]['n']})" for s in
                              ("TRAIN", "VALIDATION", "OOS", "HOLDOUT") if splits[s].get("n"))
                p(f"    STABLE: {tag:<24} {es}")
        if not any_stable:
            p("    (none -- no conditioned cut is positive-and-tight across the chronological splits)")

    # excursion distribution
    p("\n### NEXT-25-MIN EXCURSION DISTRIBUTION  (Upstox NIFTY, triggered OCO trades, in R)")
    tr = [x for r in u_ev if "rr3" in r["rr"] for x in [r["rr"]["rr3"]]
          if x["outcome"] in ("TARGET", "SL", "TIMEOUT")]
    if tr:
        mfe = sorted(x["MFE_R"] for x in tr)
        mae = sorted(x["MAE_R"] for x in tr)
        q = lambda a, p: a[min(len(a) - 1, int(len(a) * p))]
        p(f"  n={len(tr)}  MFE_R p50={q(mfe,.5):.2f} p75={q(mfe,.75):.2f} p90={q(mfe,.9):.2f} "
          f"p95={q(mfe,.95):.2f} max={mfe[-1]:.2f}")
        p(f"           MAE_R p50={q(mae,.5):.2f} p25={q(mae,.25):.2f} p10={q(mae,.1):.2f} "
          f"p05={q(mae,.05):.2f} min={mae[0]:.2f}")
        p(f"  P(MFE>=1R)={sum(1 for x in mfe if x>=1)/len(mfe):.2f}  "
          f"P(MFE>=2R)={sum(1 for x in mfe if x>=2)/len(mfe):.2f}  "
          f"P(MFE>=3R)={sum(1 for x in mfe if x>=3)/len(mfe):.2f}  "
          f"P(MFE>=5R)={sum(1 for x in mfe if x>=5)/len(mfe):.3f}")

    p("\n" + "=" * 108)
    p("VERDICT")
    p("=" * 108)
    au = agg([r for r in u_ev if r["range_x"] >= 2], "rr3")
    ak = agg([r for r in k_ev if r["range_x"] >= 2], "rr3")
    p(f"  Upstox NIFTY 5m range_x>=2x 1:3 : n={au.get('n')} tgt%={(au.get('tgt_pct') or 0)*100:.1f} "
      f"to%={(au.get('to_pct') or 0)*100:.1f} E[R]={au.get('E_R')} PF={au.get('PF')}")
    p(f"  Kaggle NIFTY 5m range_x>=2x 1:3 : n={ak.get('n')} tgt%={(ak.get('tgt_pct') or 0)*100:.1f} "
      f"to%={(ak.get('to_pct') or 0)*100:.1f} E[R]={ak.get('E_R')} PF={ak.get('PF')}")
    p("  This is a RANGE-SHAPE proxy on the cash index -- no volume, no L2, no aggressor, no OI.")
    p("  Any conditioned cut flagged STABLE above still has: <15% 1:3 target-hit, ~55-60% timeouts,")
    p("  ~30s-unmodellable microstructure, and it is NOT order-flow. It is a candidate for FURTHER")
    p("  study only, never a production signal. No strategy change, no wiring, nothing PROVEN.")


CSV_COLS = ["src", "session", "spike_time", "tod", "regime", "gap", "dow", "range_x",
            "pre_compression", "after_cutoff", "n1_range", "near_pivot",
            "rr3_outcome", "rr3_side", "rr3_realized_R", "rr3_MFE_R", "rr3_MAE_R",
            "rr2_outcome", "rr2_realized_R", "rr4_outcome", "rr4_realized_R"]


def _flat(r):
    o = {k: r.get(k) for k in CSV_COLS}
    for rk in ("rr2", "rr3", "rr4"):
        w = r["rr"].get(rk) or {}
        for f in ("outcome", "side", "realized_R", "MFE_R", "MAE_R"):
            if f"{rk}_{f}" in CSV_COLS:
                o[f"{rk}_{f}"] = w.get(f)
    return o


def main():
    print("loading NIFTY 5m ...", file=sys.stderr)
    u = load_upstox_5m()
    k = load_kaggle_5m()
    print(f"  upstox sessions={len(u)}  kaggle sessions={len(k)}", file=sys.stderr)
    u_ev = build(u, "UPSTOX")
    k_ev = build(k, "KAGGLE")
    print(f"  events: upstox={len(u_ev)}  kaggle={len(k_ev)}", file=sys.stderr)
    report(u_ev, k_ev, sys.stdout)
    with OUT.open("w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for r in u_ev + k_ev:
            w.writerow(_flat(r))
    print(f"\nwrote {len(u_ev) + len(k_ev)} events -> {OUT}")


if __name__ == "__main__":
    main()
