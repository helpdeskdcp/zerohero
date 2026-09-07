#!/usr/bin/env python3
"""
imbalance_nc_oco_breakout.py  --  RESEARCH / BACKTEST ONLY.

Variant of IMBALANCE_NEXT_CANDLE_1R3 requested 2026-09-07:

  N   = candle that is BOTH an abnormal spike AND shows an imbalance (>= T)
  N+1 = the next candle; after it CLOSES, mark its HIGH and its LOW
  then place BOTH stop orders (OCO):
        price breaks N+1 HIGH  -> BUY   (SL = N+1 LOW)
        price breaks N+1 LOW   -> SELL  (SL = N+1 HIGH)
        first level broken wins; R = N+1 HIGH - N+1 LOW
  targets 1:2 / 1:3 (primary) / 1:4 ; hard 25-minute lifetime (TIMEOUT marked
  to market at the 25-min close). No bar after +25 min is used.

Difference vs the base module: the base only trades in N+1's own colour
direction (green->long, red->short). This version arms BOTH sides and lets the
market pick.

READ-ONLY. Engine primitives (resample, spike_feats, agg, pivots, thresholds)
imported UNCHANGED from imbalance_next_candle_1r3_research.py. No change to the
frozen H1/H7 classifier, the live entry/SL/target/risk/execution path, or the
IMBALANCE_NEXT_CANDLE_1R3 calculations. No production wiring, no order.

GENUINE L2 GATE (spec section 11): no aggressor-side data exists. "Imbalance" is
a PROXY -- passive_book = resting bid_qty/ask_qty (3 futures sessions);
range/vol = abnormal-candle proxy on OHLC(+volume). Headline stays:
NOT VALIDATED -- GENUINE L2 REQUIRED. Nothing PROVEN.
"""
from __future__ import annotations

import csv as _csv
import sqlite3
import statistics as st
import sys
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "imbalance_nc_oco_breakout_events.csv"
_IST = timezone(timedelta(hours=5, minutes=30))
MAX_MIN = 25

from scripts.imbalance_next_candle_1r3_research import (   # noqa: E402  (unchanged engine)
    load_fut_1m, load_book_series, load_kaggle_nifty_5m, resample, spike_feats,
    _asof, agg, _line, _pivots, _swings, colour, THRESHOLDS, TARGETS, ROLL, _bucket,
    HDB,
)
UDB = ROOT / "data" / "historical" / "upstox" / "upstox_research.db"


def load_upstox_nifty_5m():
    if not UDB.exists():
        return {}
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
        if not ("09:15" <= t.strftime("%H:%M") <= "15:30"):
            continue
        out.setdefault(t.date().isoformat(), []).append(
            {"t": t, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": 0.0})
    for d in out:
        out[d].sort(key=lambda b: b["t"])
    return out


# ---------------------------------------------------------------- OCO walk
def _res(outcome, side, entry, R, rr, t_trig, t_exit, t0, mfe, mae, mtm=None):
    if outcome == "TARGET":
        realized = rr
    elif outcome == "SL":
        realized = -1.0
    else:
        realized = round(((mtm - entry) if side == "LONG" else (entry - mtm)) / R, 3) if mtm is not None else 0.0
    return {
        "outcome": outcome, "side": side, "entry": round(entry, 3), "R_pts": round(R, 3),
        "t_to_trigger_s": (t_trig - t0).total_seconds() if t_trig else None,
        "t_to_target_s": (t_exit - t0).total_seconds() if outcome == "TARGET" else None,
        "t_to_sl_s": (t_exit - t0).total_seconds() if outcome == "SL" else None,
        "MFE_R": round(mfe / R, 3), "MAE_R": round(mae / R, 3), "realized_R": realized,
    }


def walk_oco(bars, mark_close_t, n1, rr):
    """Both N+1 high & low armed. First break = entry; SL = the opposite mark."""
    hi, lo = n1["h"], n1["l"]
    R = hi - lo
    if R <= 0:
        return None
    deadline = mark_close_t + timedelta(minutes=MAX_MIN)
    seg = [b for b in bars if mark_close_t < b["t"] <= deadline]
    if not seg:
        return {"outcome": "NO_DATA"}
    side = t_trig = entry = sl = tgt = None
    trig_idx = None
    for k, b in enumerate(seg):
        up = b["h"] >= hi
        dn = b["l"] <= lo
        if up and dn:
            side = "LONG" if b["c"] >= b["o"] else "SHORT"   # pessimistic: bar's own direction
        elif up:
            side = "LONG"
        elif dn:
            side = "SHORT"
        if side:
            t_trig, trig_idx = b["t"], k
            if side == "LONG":
                entry, sl, tgt = hi, lo, hi + rr * R
            else:
                entry, sl, tgt = lo, hi, lo - rr * R
            break
    if side is None:
        return {"outcome": "NO_TRIGGER"}
    mfe = mae = 0.0
    last_c = None
    for b in seg[trig_idx:]:
        last_c = b["c"]
        adv = (b["h"] - entry) if side == "LONG" else (entry - b["l"])
        adc = (b["l"] - entry) if side == "LONG" else (entry - b["h"])
        mfe = max(mfe, adv); mae = min(mae, adc)
        hit_sl = (b["l"] <= sl) if side == "LONG" else (b["h"] >= sl)
        hit_tg = (b["h"] >= tgt) if side == "LONG" else (b["l"] <= tgt)
        if hit_sl and hit_tg:
            hit_tg = False                                   # pessimistic: SL first
        if hit_sl:
            return _res("SL", side, entry, R, rr, t_trig, b["t"], mark_close_t, mfe, mae)
        if hit_tg:
            return _res("TARGET", side, entry, R, rr, t_trig, b["t"], mark_close_t, mfe, mae)
    return _res("TIMEOUT", side, entry, R, rr, t_trig, deadline, mark_close_t, mfe, mae,
                mtm=(last_c if last_c is not None else entry))


# ---------------------------------------------------------------- events
def _prev_hlc(days, d):
    ds = sorted(days)
    k = ds.index(d)
    if k == 0:
        return None
    p = days[ds[k - 1]]
    return max(x["h"] for x in p), min(x["l"] for x in p), p[-1]["c"]


def build_fut(fut1m, book):
    ev = []
    for sym, days in fut1m.items():
        for d, b1 in sorted(days.items()):
            if len(b1) < ROLL + 6:
                continue
            tf5 = resample(b1, 5)
            bs = book.get(sym, {}).get(d) or []
            b_ts = [x[0] for x in bs]
            b_r1 = [x[1] for x in bs]
            piv = _pivots(*_prev_hlc(days, d)) if _prev_hlc(days, d) else {}
            for tf, bars, base in (("1M", resample(b1, 1), b1), ("5M", tf5, b1)):
                for i in range(ROLL, len(bars) - 1):
                    f = spike_feats(bars, i)
                    if not f:
                        continue
                    N, N1 = bars[i], bars[i + 1]
                    abn_range = f["range_x"] >= 1.5
                    vol_x = f.get("vol_x")
                    pb = _asof(b_ts, b_r1, N.get("closed_at") or N["t"]) if bs else None
                    pb_dir = None
                    if pb is not None:
                        pb_dir = pb if colour(N) == "GREEN" else (1.0 / pb if pb > 0 else None)
                    for proxy, ratio, gate in (
                        ("spike_AND_book", pb_dir, abn_range and pb_dir is not None),
                        ("book_only", pb_dir, pb_dir is not None),
                        ("vol_range", vol_x, abn_range and vol_x is not None),
                    ):
                        if not gate or ratio is None or ratio < THRESHOLDS[0]:
                            continue
                        ev.append(_mk(sym, d, tf, proxy, ratio, N, N1, base, f, piv))
    return ev


def build_index(days, label, tf_min):
    ev = []
    for d, base in sorted(days.items()):
        if len(base) < ROLL + 6:
            continue
        for tf, m in tf_min.items():
            bars = resample(base, m)
            piv = _pivots(*_prev_hlc(days, d)) if _prev_hlc(days, d) else {}
            for i in range(ROLL, len(bars) - 1):
                f = spike_feats(bars, i)
                if not f or f["range_x"] < THRESHOLDS[0]:
                    continue
                ev.append(_mk(label, d, tf, "range_only", f["range_x"],
                              bars[i], bars[i + 1], base, f, piv))
    return ev


def _mk(sym, d, tf, proxy, ratio, N, N1, base, f, piv):
    lv = dict(piv)
    near = min((abs(N["c"] - v) for v in lv.values()), default=None)
    row = {
        "symbol": sym, "session": d, "tf": tf, "proxy": proxy,
        "imb_ratio": round(ratio, 3), "imb_bucket": _bucket(ratio),
        "imb_colour": colour(N), "n1_colour": colour(N1),
        "n1_high": N1["h"], "n1_low": N1["l"], "n1_range": round(N1["h"] - N1["l"], 3),
        "range_x": round(f["range_x"], 3), "pre_compression": f.get("pre_compression"),
        "near_level_dist": round(near, 3) if near is not None else None,
        "rr": {},
    }
    for rr in TARGETS:
        w = walk_oco(base, N1.get("closed_at") or N1["t"], N1, rr)
        if w and w.get("outcome") != "NO_DATA":
            row["rr"][f"rr{int(rr)}"] = w
    return row


# ---------------------------------------------------------------- report
def _agg(recs, rk):
    w = [r["rr"][rk] for r in recs if rk in r["rr"]]
    trig = [x for x in w if x["outcome"] in ("TARGET", "SL", "TIMEOUT")]
    n = len(trig)
    if not n:
        return {"signals": len(recs), "triggered": 0}
    tgt = [x for x in trig if x["outcome"] == "TARGET"]
    slh = [x for x in trig if x["outcome"] == "SL"]
    to = [x for x in trig if x["outcome"] == "TIMEOUT"]
    rz = [x["realized_R"] for x in trig]
    wins = [x for x in rz if x > 0]; losses = [x for x in rz if x < 0]
    longs = [x for x in trig if x["side"] == "LONG"]
    mcl = cur = 0
    for r in sorted(recs, key=lambda r: (r["session"], r["tf"])):
        x = r["rr"].get(rk)
        if not x or x["outcome"] not in ("TARGET", "SL", "TIMEOUT"):
            continue
        if x["realized_R"] < 0:
            cur += 1; mcl = max(mcl, cur)
        elif x["realized_R"] > 0:
            cur = 0
    ttt = [x["t_to_trigger_s"] for x in w if x.get("t_to_trigger_s") is not None]
    return {
        "signals": len(recs), "triggered": n,
        "target_hit_pct": round(len(tgt) / n, 3), "sl_pct": round(len(slh) / n, 3),
        "timeout_pct": round(len(to) / n, 3),
        "long_share": round(len(longs) / n, 3),
        "expectancy": round(st.fmean(rz), 3),
        "profit_factor": round(sum(wins) / -sum(losses), 3) if losses else None,
        "avg_MFE_R": round(st.fmean(x["MFE_R"] for x in trig), 3),
        "avg_MAE_R": round(st.fmean(x["MAE_R"] for x in trig), 3),
        "max_consec_losses": mcl,
        "t_to_trigger_med_s": round(st.median(ttt), 1) if ttt else None,
        "sessions": len({r["session"] for r in recs}),
        # reuse the base _line formatter's expected keys:
        "win_rate": round(len(wins) / n, 3), "avg_R": round(st.fmean(rz), 3),
        "net_R": round(sum(rz), 2), "max_DD_R": None, "MFE_R_med": round(st.fmean(x["MFE_R"] for x in trig), 3),
        "MAE_R_med": round(st.fmean(x["MAE_R"] for x in trig), 3),
        "p_reach_1R": None, "p_reach_3R": None, "p_sl_first": round(len(slh) / n, 3),
    }


def _l(tag, m):
    if not m.get("triggered"):
        return f"  {tag:<40} sig={m.get('signals', 0):>4} trig=0"
    return (f"  {tag:<40} sig={m['signals']:>4} trig={m['triggered']:>4} ses={m['sessions']} "
            f"L%={m['long_share']*100:>4.0f} tgt%={m['target_hit_pct']*100:>5.1f} sl%={m['sl_pct']*100:>5.1f} "
            f"to%={m['timeout_pct']*100:>5.1f} E[R]={m['expectancy']:>6.2f} PF={m['profit_factor']} "
            f"MFE~{m['avg_MFE_R']:>5.2f} MAE~{m['avg_MAE_R']:>6.2f} mcl={m['max_consec_losses']} "
            f"t2trig={m['t_to_trigger_med_s']}")


def _split(recs, four=True):
    ds = sorted({r["session"] for r in recs})
    if len(ds) < (4 if four else 3):
        return {d: "ALL" for d in ds}
    if four:
        a, b, c = int(len(ds) * .45), int(len(ds) * .65), int(len(ds) * .82)
        return {d: ("TRAIN" if k < a else "VALIDATION" if k < b else "OOS" if k < c else "HOLDOUT")
                for k, d in enumerate(ds)}
    a, b = int(len(ds) * .5), int(len(ds) * .75)
    return {d: ("TRAIN" if k < a else "VALIDATION" if k < b else "OOS") for k, d in enumerate(ds)}


def report(fut, kag, ups, out):
    p = lambda *a: print(*a, file=out)
    p("=" * 108)
    p("OCO N+1 BREAKOUT after ABNORMAL SPIKE + IMBALANCE  --  BACKTEST (READ-ONLY, PROXY)")
    p("N = abnormal-spike & imbalance candle. Mark N+1 high & low. Break high -> BUY (SL N+1 low),")
    p("break low -> SELL (SL N+1 high). R = N+1 range. 1:2 / 1:3 / 1:4. 25-min lifetime.")
    p("Imbalance is a PROXY (no aggressor data). Headline: NOT VALIDATED -- GENUINE L2 REQUIRED.")
    p("=" * 108)

    for name, ev, four in (("FUTURES (NIFTY/CRUDEOIL/NATURALGAS, 4 sessions 2026-09-01..04)", fut, False),
                           ("KAGGLE NIFTY 5m (2015..2026)", kag, True),
                           ("UPSTOX NIFTY 5m (2022..2026)", ups, True)):
        if not ev:
            continue
        s = sorted({r["session"] for r in ev})
        p(f"\n{name}  --  {len(ev)} events | {len(s)} sessions {s[0]}..{s[-1]}")
        spl = _split(ev, four)
        proxies = sorted({r["proxy"] for r in ev})
        p("  -- primary 1:3 by proxy x cumulative threshold (ratio >= T) --")
        for px in proxies:
            for thr, lab in zip(THRESHOLDS, ("200%", "300%", "400%", "500%")):
                sub = [r for r in ev if r["proxy"] == px and r["imb_ratio"] >= thr]
                if sub:
                    p(_l(f"{px} >= {lab}", _agg(sub, "rr3")))
        p("  -- target 1:2 / 1:3 / 1:4 (all events pooled) --")
        for rk, lab in (("rr2", "1:2"), ("rr3", "1:3"), ("rr4", "1:4")):
            p(_l(f"target {lab}", _agg(ev, rk)))
        p("  -- by timeframe (1:3) --")
        for tf in sorted({r["tf"] for r in ev}):
            p(_l(f"tf {tf}", _agg([r for r in ev if r["tf"] == tf], "rr3")))
        p("  -- near S/R/pivot vs away (1:3) --")
        nn = [r for r in ev if r.get("near_level_dist") is not None
              and abs(r["near_level_dist"]) <= 0.0015 * (abs(r["n1_high"]) or 1)]
        p(_l("near", _agg(nn, "rr3")))
        p(_l("away", _agg([r for r in ev if r not in nn], "rr3")))
        p("  -- sideways->spike (pre_compression<0.8) vs normal (1:3) --")
        sq = [r for r in ev if (r.get("pre_compression") or 9) < 0.8]
        p(_l("SIDEWAYS->SPIKE", _agg(sq, "rr3")))
        p(_l("NORMAL", _agg([r for r in ev if r not in sq], "rr3")))
        p("  -- chronological split (1:3) --")
        for tag in ("TRAIN", "VALIDATION", "OOS", "HOLDOUT", "ALL"):
            sub = [r for r in ev if spl.get(r["session"]) == tag]
            if sub:
                p(_l(tag, _agg(sub, "rr3")))
        oos = sorted(d for d, t in spl.items() if t in ("OOS", "HOLDOUT"))
        if len(oos) >= 5:
            latest = set(oos[int(len(oos) * .8):])
            p(_l("latest 20% OOS+HOLDOUT", _agg([r for r in ev if r["session"] in latest], "rr3")))

    p("\n" + "=" * 108)
    p("VERDICT")
    p("=" * 108)
    fb = _agg([r for r in fut if r["proxy"] == "spike_AND_book" and r["imb_ratio"] >= 2.0], "rr3")
    fv = _agg([r for r in fut if r["proxy"] == "vol_range" and r["imb_ratio"] >= 2.0], "rr3")
    kg = _agg([r for r in kag if r["imb_ratio"] >= 2.0], "rr3")
    up = _agg([r for r in ups if r["imb_ratio"] >= 2.0], "rr3")
    p("  Genuine aggressor-side L2: NONE. => NOT VALIDATED -- GENUINE L2 REQUIRED. Imbalance is a proxy.")
    p(f"  spike_AND_book >=200% (futures, 3-4 sess): trig={fb.get('triggered',0)} "
      f"tgt%={(fb.get('target_hit_pct') or 0)*100:.1f} sl%={(fb.get('sl_pct') or 0)*100:.1f} "
      f"to%={(fb.get('timeout_pct') or 0)*100:.1f} E[R]={fb.get('expectancy')} PF={fb.get('profit_factor')}")
    p(f"  vol_range >=200% (futures): trig={fv.get('triggered',0)} E[R]={fv.get('expectancy')} PF={fv.get('profit_factor')}")
    p(f"  range_only >=200% Kaggle NIFTY 5m: trig={kg.get('triggered',0)} tgt%={(kg.get('target_hit_pct') or 0)*100:.1f} "
      f"to%={(kg.get('timeout_pct') or 0)*100:.1f} E[R]={kg.get('expectancy')} PF={kg.get('profit_factor')}")
    p(f"  range_only >=200% Upstox NIFTY 5m: trig={up.get('triggered',0)} tgt%={(up.get('target_hit_pct') or 0)*100:.1f} "
      f"to%={(up.get('timeout_pct') or 0)*100:.1f} E[R]={up.get('expectancy')} PF={up.get('profit_factor')}")
    p("  OCO (both sides armed) vs directional (N+1 colour, base module): compare to")
    p("  IMBALANCE_NEXT_CANDLE_1R3_MTF_OI.md. No config is selected (spec section 10); tiny/1-regime")
    p("  futures samples and a timeout-dominated index proxy. No strategy change, no production")
    p("  wiring, frozen H1/H7 + live logic untouched. Nothing PROVEN.")


CSV_COLS = ["symbol", "session", "tf", "proxy", "imb_ratio", "imb_bucket", "imb_colour",
            "n1_colour", "n1_high", "n1_low", "n1_range", "range_x", "pre_compression",
            "near_level_dist", "rr3_outcome", "rr3_side", "rr3_realized_R", "rr3_MFE_R",
            "rr3_MAE_R", "rr3_t_to_trigger_s", "rr3_t_to_target_s", "rr3_t_to_sl_s",
            "rr2_outcome", "rr2_side", "rr2_realized_R", "rr4_outcome", "rr4_side", "rr4_realized_R"]


def _flat(r):
    o = {k: r.get(k) for k in CSV_COLS}
    for rk in ("rr2", "rr3", "rr4"):
        w = r["rr"].get(rk) or {}
        for f in ("outcome", "side", "realized_R", "MFE_R", "MAE_R",
                  "t_to_trigger_s", "t_to_target_s", "t_to_sl_s"):
            if f"{rk}_{f}" in CSV_COLS:
                o[f"{rk}_{f}"] = w.get(f)
    return o


def main():
    if not HDB.exists():
        sys.exit(f"[STOP] {HDB} not found")
    con = sqlite3.connect(f"file:{HDB}?mode=ro", uri=True)
    print("loading ...", file=sys.stderr)
    fut1m = load_fut_1m(con)
    book = load_book_series(con)
    con.close()
    kag5 = load_kaggle_nifty_5m()
    ups5 = load_upstox_nifty_5m()
    fut = build_fut(fut1m, book)
    kag = build_index(kag5, "KAGGLE_NIFTY", {"5M": 5, "30M": 30})
    ups = build_index(ups5, "UPSTOX_NIFTY", {"5M": 5, "30M": 30})
    print(f"  events: fut={len(fut)} kaggle={len(kag)} upstox={len(ups)}", file=sys.stderr)
    report(fut, kag, ups, sys.stdout)
    with OUT.open("w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for r in fut + kag + ups:
            w.writerow(_flat(r))
    print(f"\nwrote {len(fut) + len(kag) + len(ups)} events -> {OUT}")


if __name__ == "__main__":
    main()
