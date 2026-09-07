#!/usr/bin/env python3
"""
imbalance_nc_1r3_upstox_nifty.py  --  RESEARCH / BACKTEST ONLY.

Runs the EXISTING IMBALANCE_NEXT_CANDLE_1R3 engine (imported UNCHANGED from
scripts/imbalance_next_candle_1r3_research.py -- same spike_feats, resample,
walk_n1, agg, pivots, MTF-confirm) on the Upstox NIFTY historical bars:

  * NIFTY 5m  2022-01-03 .. 2026-09-04   (1,160 sessions)  -- cross-vendor /
    cross-period replication of the Kaggle range-shape proxy finding.
  * NIFTY 1m  2026-08-07 .. 2026-09-04   (21 sessions)     -- the only INDEX
    series with 1m bars, so the 1M/5M/30M confirmation configs A-G can be
    computed on index data.

READ-ONLY. No production wiring. Nothing changed on the frozen H1/H7 classifier,
the live entry/SL/target/risk/execution path, or the IMBALANCE_NEXT_CANDLE_1R3
calculations (this file only feeds them another dataset).

GENUINE L2 GATE (spec section 11): Upstox NIFTY cash-index bars have NO volume,
NO bid/ask, NO depth, NO aggressor side, NO per-strike OI. The only proxy
available here is `range_only` = abnormal candle range vs a rolling baseline.
Headline is therefore fixed: NOT VALIDATED -- GENUINE L2 REQUIRED. This run only
tells us whether the range-shape N+1 behaviour REPLICATES across a second vendor
and period; it is not an L2 result and nothing is PROVEN.
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
OUT = ROOT / "data" / "imbalance_nc_1r3_upstox_nifty_events.csv"
_IST = timezone(timedelta(hours=5, minutes=30))

from scripts.imbalance_next_candle_1r3_research import (   # noqa: E402  (engine, unchanged)
    resample, spike_feats, walk_n1, agg, _line, _pivots, _swings, _mtf_confirm,
    colour, THRESHOLDS, TARGETS, ROLL, _bucket,
)


def load_upstox_nifty(tf: str):
    """{date: [ {t(IST),o,h,l,c,v=0} ascending ]} for source='upstox' NIFTY `tf`."""
    con = sqlite3.connect(f"file:{UDB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT timestamp, open, high, low, close FROM normalized_bars "
        "WHERE source='upstox' AND symbol='NIFTY' AND timeframe=? ORDER BY timestamp", (tf,)).fetchall()
    con.close()
    out: dict = {}
    for ts, o, h, l, c in rows:
        if None in (o, h, l, c):
            continue
        t = datetime.fromisoformat(ts).astimezone(_IST)
        if not ("09:15" <= t.strftime("%H:%M") <= "15:30"):
            continue
        out.setdefault(t.date().isoformat(), []).append(
            {"t": t, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": 0.0})
    for d in out:
        out[d].sort(key=lambda b: b["t"])
    return out


def _prev_hlc(days, d):
    ds = sorted(days)
    k = ds.index(d)
    if k == 0:
        return None
    p = days[ds[k - 1]]
    return max(x["h"] for x in p), min(x["l"] for x in p), p[-1]["c"]


def build(days, src_label, tf_set):
    """One event per abnormal-range candle N on each timeframe in tf_set."""
    ev = []
    for d, base in sorted(days.items()):
        if len(base) < ROLL + 6:
            continue
        tfbars = {tf: resample(base, m) for tf, m in tf_set.items()}
        tf_ts = {tf: [x["t"] for x in tfbars[tf]] for tf in tf_set}
        prev = _prev_hlc(days, d)
        piv = _pivots(*prev) if prev else {}
        for tf, bars in tfbars.items():
            for i in range(ROLL, len(bars) - 1):
                f = spike_feats(bars, i)
                if not f or f["range_x"] < THRESHOLDS[0]:
                    continue
                N, N1 = bars[i], bars[i + 1]
                lv = dict(piv)
                shi, slo = _swings(bars, i)
                if shi:
                    lv["SWING_HI"] = shi
                if slo:
                    lv["SWING_LO"] = slo
                near = min((abs(N["c"] - v) for v in lv.values()), default=None)
                conf = _mtf_confirm(tfbars, tf_ts, N.get("closed_at") or N["t"], colour(N))
                conf.discard(tf)
                row = {
                    "src": src_label, "session": d, "tf": tf, "proxy": "range_only",
                    "imb_ratio": round(f["range_x"], 3), "imb_bucket": _bucket(f["range_x"]),
                    "imb_colour": colour(N), "n1_colour": colour(N1), "direction": colour(N1),
                    "n1_high": N1["h"], "n1_low": N1["l"], "n1_range": round(N1["h"] - N1["l"], 3),
                    "pre_compression": f.get("pre_compression"),
                    "mtf_confirm": "+".join(sorted(conf)) or "-", "n_mtf_confirm": len(conf),
                    "near_level_dist": round(near, 3) if near is not None else None,
                    "rr_res": {},
                }
                for rr in TARGETS:
                    w = walk_n1(base, N1.get("closed_at") or N1["t"], N1, rr)
                    if w and w.get("outcome") != "NO_DATA":
                        if "entry" in w:
                            w["dist_to_entry"] = round(abs(w["entry"] - N["c"]), 3)
                        row["rr_res"][f"rr{int(rr)}"] = w
                ev.append(row)
    return ev


def _is_near(r):
    d = r.get("near_level_dist")
    ref = abs(r["n1_high"]) or 1.0
    return d is not None and abs(d) <= 0.0015 * ref


def _split(recs):
    ds = sorted({r["session"] for r in recs})
    if len(ds) < 4:
        return {d: "ALL" for d in ds}
    a, b, c = int(len(ds) * 0.45), int(len(ds) * 0.65), int(len(ds) * 0.82)
    return {d: ("TRAIN" if k < a else "VALIDATION" if k < b else "OOS" if k < c else "HOLDOUT")
            for k, d in enumerate(ds)}


def report(ev5, ev1, out):
    p = lambda *a: print(*a, file=out)
    p("=" * 104)
    p("IMBALANCE_NEXT_CANDLE_1R3  --  BACKTEST ON UPSTOX NIFTY HISTORICAL (READ-ONLY, PROXY)")
    p("Engine imported UNCHANGED from imbalance_next_candle_1r3_research.py. Upstox NIFTY is a")
    p("cash index: NO volume / bid-ask / depth / aggressor / per-strike OI. Only the range_only")
    p("shape proxy applies. Per spec section 11: NOT VALIDATED -- GENUINE L2 REQUIRED. This run only")
    p("checks whether the range-shape N+1 behaviour REPLICATES vs the Kaggle NIFTY proxy.")
    p("=" * 104)

    for tag, ev in (("UPSTOX NIFTY 5m  (2022-01..2026-09)", ev5),
                    ("UPSTOX NIFTY 1m  (2026-08-07..09-04)", ev1)):
        if not ev:
            continue
        sess = sorted({r["session"] for r in ev})
        p(f"\n{tag}  --  {len(ev)} proxy events | {len(sess)} sessions {sess[0]}..{sess[-1]}")
        spl = _split(ev)
        p("  -- 1:3 by CUMULATIVE range threshold (range_x >= T) --")
        for thr, lab in zip(THRESHOLDS, ("200%", "300%", "400%", "500%")):
            sub = [r for r in ev if r["imb_ratio"] >= thr]
            if sub:
                p(_line(f"range_x >= {lab}", agg(sub, "rr3")))
        p("  -- target 1:2 / 1:3 / 1:4 (>=200%, pooled) --")
        for rk, lab in (("rr2", "1:2"), ("rr3", "1:3"), ("rr4", "1:4")):
            p(_line(f"target {lab}", agg(ev, rk)))
        p("  -- timeframe configs A-G (1:3) --")
        cfgs = [
            ("A 1M only", lambda r: r["tf"] == "1M"),
            ("B 5M only", lambda r: r["tf"] == "5M"),
            ("C 30M only", lambda r: r["tf"] == "30M"),
            ("D 1M+5M agree", lambda r: r["tf"] == "1M" and "5M" in r["mtf_confirm"]),
            ("E 1M+30M agree", lambda r: r["tf"] == "1M" and "30M" in r["mtf_confirm"]),
            ("F 5M+30M agree", lambda r: r["tf"] == "5M" and "30M" in r["mtf_confirm"]),
            ("G 1M+5M+30M agree", lambda r: r["tf"] == "1M" and "5M" in r["mtf_confirm"] and "30M" in r["mtf_confirm"]),
        ]
        for name, sel in cfgs:
            sub = [r for r in ev if sel(r)]
            if sub:
                p(_line(f"TF {name}", agg(sub, "rr3")))
        p("  -- near S/R/pivot vs away (1:3) --")
        p(_line("near", agg([r for r in ev if _is_near(r)], "rr3")))
        p(_line("away", agg([r for r in ev if not _is_near(r)], "rr3")))
        p("  -- sideways->spike (pre_compression<0.8) vs normal (1:3) --")
        p(_line("SIDEWAYS->SPIKE", agg([r for r in ev if (r.get("pre_compression") or 9) < 0.8], "rr3")))
        p(_line("NORMAL", agg([r for r in ev if (r.get("pre_compression") or 9) >= 0.8], "rr3")))
        p("  -- chronological split (1:3) --")
        for s in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT", "ALL"):
            sub = [r for r in ev if spl.get(r["session"]) == s]
            if sub:
                p(_line(s, agg(sub, "rr3")))
        oos = sorted(d for d, s in spl.items() if s in ("OOS", "HOLDOUT"))
        if len(oos) >= 5:
            latest = set(oos[int(len(oos) * 0.8):])
            p(_line("latest 20% of OOS+HOLDOUT", agg([r for r in ev if r["session"] in latest], "rr3")))

    p("\n" + "=" * 104)
    p("VERDICT")
    p("=" * 104)
    a5 = agg([r for r in ev5 if r["imb_ratio"] >= 2.0], "rr3") if ev5 else {}
    p(f"  Genuine L2 / aggressor-side data on Upstox NIFTY: NONE (cash index OHLC only).")
    p(f"  => NOT VALIDATED -- GENUINE L2 REQUIRED.")
    p(f"  range_only proxy, Upstox NIFTY 5m >=200%: n={a5.get('triggered', 0)} "
      f"tgt%={(a5.get('target_hit_pct') or 0)*100:.1f} SL%={(a5.get('sl_pct') or 0)*100:.1f} "
      f"TO%={(a5.get('timeout_pct') or 0)*100:.1f} E[R]={a5.get('expectancy')} PF={a5.get('profit_factor')}")
    p("  Compare to Kaggle NIFTY 5m range_only >=200% (IMBALANCE_NEXT_CANDLE_1R3_MTF_OI.md): "
      "tgt~9%, TO~55%, E[R] +0.21 decaying to +0.10 latest-OOS.")
    p("  If the two vendors AGREE -> the range-shape N+1 pattern is a consistent (still proxy-only,")
    p("  timeout-dominated, <10%-target-hit) artifact, NOT an L2 edge. If they DISAGREE -> even the")
    p("  proxy is unstable. Either way: no strategy change, no production wiring, nothing PROVEN.")


CSV_COLS = ["src", "session", "tf", "proxy", "imb_ratio", "imb_bucket", "imb_colour",
            "n1_colour", "direction", "n1_high", "n1_low", "n1_range", "pre_compression",
            "mtf_confirm", "n_mtf_confirm", "near_level_dist",
            "rr3_outcome", "rr3_realized_R", "rr3_MFE_R", "rr3_MAE_R",
            "rr3_t_to_entry_s", "rr3_t_to_target_s", "rr3_t_to_sl_s",
            "rr2_outcome", "rr2_realized_R", "rr4_outcome", "rr4_realized_R"]


def _flat(r):
    o = {k: r.get(k) for k in CSV_COLS}
    for rk in ("rr2", "rr3", "rr4"):
        w = r["rr_res"].get(rk) or {}
        for f in ("outcome", "realized_R", "MFE_R", "MAE_R", "t_to_entry_s", "t_to_target_s", "t_to_sl_s"):
            if f"{rk}_{f}" in CSV_COLS:
                o[f"{rk}_{f}"] = w.get(f)
    return o


def main():
    if not UDB.exists():
        sys.exit(f"[STOP] {UDB} not found")
    print("loading Upstox NIFTY 5m + 1m ...", file=sys.stderr)
    d5 = load_upstox_nifty("5m")
    d1 = load_upstox_nifty("1m")
    print(f"  5m sessions={len(d5)}  1m sessions={len(d1)}", file=sys.stderr)
    ev5 = build(d5, "upstox_nifty_5m", {"5M": 5, "30M": 30})
    ev1 = build(d1, "upstox_nifty_1m", {"1M": 1, "5M": 5, "30M": 30})
    print(f"  events: 5m={len(ev5)}  1m={len(ev1)}", file=sys.stderr)
    report(ev5, ev1, sys.stdout)
    with OUT.open("w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for r in ev5 + ev1:
            w.writerow(_flat(r))
    print(f"\nwrote {len(ev5) + len(ev1)} events -> {OUT}")


if __name__ == "__main__":
    main()
