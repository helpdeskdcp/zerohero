"""Internal parity audit: engine.py (event log, execution = the Pine script's own) vs reference_literal.py (statement-by-statement
transliteration of pine/hybrid_mp_v13.pine). External (TradingView) parity is NOT testable here (no trade export, Pine cannot be run).
Usage: python scripts/hybrid_mp_parity.py   -> data/research/hybrid_mp/audit/PARITY_DISCREPANCIES.csv + parity_summary.json"""
import csv
import datetime as dt
import hashlib
import json
import sys
import time

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import Params, resample, run  # noqa: E402
from app.research_engines.hybrid_mp.reference_literal import run_literal  # noqa: E402
from app.research_engines.smart_bos.data import ist_minutes, load_1m  # noqa: E402

OUT = "data/research/hybrid_mp/audit"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
ASIS = dict(fill="close", gap_fills=False, eod_flat=False, exit_model="PINE")      # = what the Pine script itself does

CONFIGS = {
    "C0_defaults": {},
    "C1_retest_only": {"entryMode": "Breakout + Retest"},
    "C2_mtf_3of4": {"requireMTF": True},
    "C3_vwap": {"useVWAP": True},
    "C4_pattern_filter": {"usePatternFilter": True},
    "C5_prev_low_sl": {"useCandleSL": False},
    "C6_pivot3_cont3_retest4": {"pivotLen": 3, "continuationLookback": 3, "retestBars": 4},
    "C7_all_filters": {"requireMTF": True, "useVWAP": True, "usePatternFilter": True, "minAgree": 2},
}


def to_params(cfg, tf, session, mintick, ties="tv"):
    m = dict(pivotLen="pivot_len", atrLen="atr_len", retestBars="retest_bars", retestTolATR="retest_tol_atr", useVWAP="use_vwap",
             useCandleSL="use_candle_sl", usePatternFilter="use_pattern_filter", continuationLookback="continuation_lookback",
             requireMTF="require_mtf", minAgree="min_agree", mtfLookback="mtf_lookback")
    kw = {m[k]: v for k, v in cfg.items() if k in m}
    if "entryMode" in cfg:
        kw["entry_mode"] = "hybrid" if cfg["entryMode"].startswith("Hybrid") else "retest"
    return Params(chart_tf=tf, session=session, mintick=mintick, pivot_ties=ties, **kw, **ASIS)


def last_sessions(b, k):
    days, _ = ist_minutes(b["t"])
    keep = np.isin(days, np.unique(days)[-k:])
    return {x: y[keep] for x, y in b.items()}


def synth(seed, sessions=70, hole_frac=0.02):
    r = np.random.default_rng(seed)
    t0 = int(dt.datetime(2026, 1, 5, 9, 15, tzinfo=IST).timestamp())
    T, O, H, L, C, V = [], [], [], [], [], []
    px, d = 22000.0, 0
    while len(set(x // 86400 for x in T)) < sessions:
        base = t0 + d * 86400
        d += 1
        if dt.datetime.fromtimestamp(base, IST).weekday() >= 5:
            continue
        px += r.normal(0, 40)                                   # overnight gap
        for m in range(375):
            o = px
            px = px + r.normal(0, 3.5)
            T.append(base + m * 60); O.append(o); C.append(px)
            H.append(max(o, px) + abs(r.normal(0, 1.5))); L.append(min(o, px) - abs(r.normal(0, 1.5))); V.append(float(r.integers(50, 500)))
    keep = r.random(len(T)) >= hole_frac                         # missing candles
    a = lambda x, dtp=float: np.array(x, dtype=dtp)[keep]
    return {"t": a(T, np.int64), "o": a(O), "h": a(H), "l": a(L), "c": a(C), "v": a(V)}


def datasets():
    d = {}
    for sym, tf, k in (("NIFTY", 5, 250), ("NIFTY", 1, 45), ("BANKNIFTY", 5, 250), ("SENSEX", 5, 250)):
        b1 = last_sessions(load_1m(sym, "INDEX"), k if tf == 1 else min(k, 250))
        d[f"{sym}_{tf}m_last{k}s"] = (b1, tf, (555, 930), 0.05)
    c1 = load_1m("CRUDEOIL", "FUTURE")
    d["CRUDEOIL_FUT_1m_MCX"] = (c1, 1, (540, 1410), 0.05)
    b = synth(11)
    d["SYNTH_RW_vol_holes_1m"] = (b, 1, (555, 930), 0.05)
    d["SYNTH_RW_vol_holes_5m"] = (synth(12), 5, (555, 930), 0.05)
    return d


def norm(e):
    return tuple(float(x) if isinstance(x, (float, np.floating)) else x for x in e)


def same(x, y, tol=1e-7):
    """event lists equal, floats compared with an absolute tolerance (1e-7 price units) instead of rounding at a boundary"""
    if len(x) != len(y):
        return False
    for a, b in zip(x, y):
        if len(a) != len(b):
            return False
        for u, v in zip(a, b):
            if isinstance(u, float) or isinstance(v, float):
                if not (isinstance(u, float) and isinstance(v, float)) or abs(u - v) > tol:
                    return False
            elif u != v:
                return False
    return True


def compare(a, b):
    """a = reference (Pine literal), b = engine. Returns list of (bar, ref_event, engine_event, type)."""
    from collections import defaultdict
    ga, gb = defaultdict(list), defaultdict(list)
    for e in a:
        ga[e[0]].append(norm(e))
    for e in b:
        gb[e[0]].append(norm(e))
    out = []
    for i in sorted(set(ga) | set(gb)):
        x, y = ga.get(i, []), gb.get(i, [])
        if same(x, y):
            continue
        if sorted(map(str, [e[:2] for e in x])) == sorted(map(str, [e[:2] for e in y])) and sorted(map(lambda e: round(sum(v for v in e[2:] if isinstance(v, float)), 4), x)) == sorted(map(lambda e: round(sum(v for v in e[2:] if isinstance(v, float)), 4), y)):
            out.append((i, x, y, "ORDER_ONLY"))
            continue
        kx, ky = {e[1] for e in x}, {e[1] for e in y}
        if kx - ky:
            typ = "MISSING_IN_PYTHON"
        elif ky - kx:
            typ = "EXTRA_IN_PYTHON"
        else:
            typ = "VALUE_DIFF"
        out.append((i, x, y, typ))
    return out


def main():
    rows, summary = [], []
    data = datasets()
    meta = {k: {"bars_1m": int(len(v[0]["t"])), "first": dt.datetime.fromtimestamp(int(v[0]["t"][0]), IST).isoformat(),
                "last": dt.datetime.fromtimestamp(int(v[0]["t"][-1]), IST).isoformat(), "chart_tf": v[1], "session": list(v[2])}
            for k, v in data.items()}
    for dname, (b1, tf, session, mintick) in data.items():
        om, cm = session
        chart = resample(b1, tf, om, cm)
        cv = chart["v"]
        for cname, cfg in CONFIGS.items():
            variants = [("tv", "post-fix")] + ([("allow", "PRE-FIX (engine pivot_ties=allow)")] if cname == "C0_defaults" else [])
            lit_cfg = dict(cfg, chart_tf=tf, session=session, mintick=mintick)
            t0 = time.time()
            ref = run_literal(chart["t"], chart["o"], chart["h"], chart["l"], chart["c"], cv, b1, lit_cfg)
            for ties, label in variants:
                p = to_params(cfg, tf, session, mintick, ties)
                tr, st = run(chart, p, b1=b1, want_events=True)
                diffs = compare(ref, st["events"])
                s = dict(dataset=dname, config=cname, engine_variant=label, chart_bars=len(chart["t"]), ref_events=len(ref),
                         engine_events=len(st["events"]), signals=sum(1 for e in ref if e[1] == "SIGNAL"), mismatching_bars=len(diffs),
                         by_type={t_: sum(1 for d in diffs if d[3] == t_) for t_ in {d[3] for d in diffs}},
                         result="EXACT MATCH" if not diffs else "MISMATCH", seconds=round(time.time() - t0, 1))
                summary.append(s)
                for i, x, y, typ in diffs[:200]:
                    rows.append([dname, cname, label, i, dt.datetime.fromtimestamp(int(chart["t"][i]), IST).strftime("%Y-%m-%d %H:%M"),
                                 json.dumps(x), json.dumps(y), typ,
                                 "pivot tie rule differs (engine allowed right-side ties; TradingView does not)" if "PRE-FIX" in label else ""])
                print(f"{dname:26s} {cname:24s} {label:34s} events ref={len(ref):6d} eng={len(st['events']):6d} signals={s['signals']:5d} -> {s['result']} {s['by_type']}", flush=True)
    with open(f"{OUT}/PARITY_DISCREPANCIES.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dataset", "config", "engine_variant", "bar_index", "bar_open_ist", "pine_literal_events", "python_engine_events", "discrepancy_type", "explanation"])
        w.writerows(rows)
    src = open("app/research_engines/hybrid_mp/pine/hybrid_mp_v13.pine", "rb").read()
    json.dump(dict(pine_sha256=hashlib.sha256(src).hexdigest(), datasets=meta, runs=summary), open(f"{OUT}/parity_summary.json", "w"), indent=1)
    post = [s for s in summary if s["engine_variant"] == "post-fix"]
    print(f"\nPOST-FIX comparisons: {len(post)} | exact: {sum(s['result']=='EXACT MATCH' for s in post)} | mismatching: {sum(s['result']!='EXACT MATCH' for s in post)}"
          f" | total signals compared: {sum(s['signals'] for s in post)} | discrepancy rows written: {len(rows)}")


def legacy_weekday_ledger():
    """Evidence for the weekday defect: re-run one comparison with the OLD (off-by-one) weekday formula monkeypatched in."""
    import app.research_engines.hybrid_mp.engine as eng
    eng.weekday_ist = lambda t: (((np.asarray(t) + 19800) // 86400) + 4) % 7          # the buggy formula, test-only
    b1 = last_sessions(load_1m("NIFTY", "INDEX"), 250)
    chart = resample(b1, 5, 555, 930)
    ref = run_literal(chart["t"], chart["o"], chart["h"], chart["l"], chart["c"], chart["v"], b1, dict(chart_tf=5, session=(555, 930), mintick=0.05))
    tr, st = run(chart, to_params({}, 5, (555, 930), 0.05), b1=b1, want_events=True)
    diffs = compare(ref, st["events"])
    wd = lambda i: dt.datetime.fromtimestamp(int(chart["t"][i]), IST).strftime("%a")
    with open(f"{OUT}/PARITY_DISCREPANCIES_pre_weekday_fix.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dataset", "config", "engine_variant", "bar_index", "bar_open_ist", "weekday", "pine_literal_events", "python_engine_events", "discrepancy_type"])
        for i, x, y, typ in diffs[:2000]:
            w.writerow(["NIFTY_5m_last250s", "C0_defaults", "PRE-FIX (weekday off by one)", i, dt.datetime.fromtimestamp(int(chart["t"][i]), IST).strftime("%Y-%m-%d %H:%M"),
                        wd(i), json.dumps(x), json.dumps(y), typ])
    days = {}
    for i, x, y, typ in diffs:
        days[wd(i)] = days.get(wd(i), 0) + 1
    print(f"legacy weekday formula: {len(diffs)} mismatching bars of {len(chart['t'])}; by weekday: {days}; python signals {sum(1 for e in st['events'] if e[1]=='SIGNAL')} vs reference {sum(1 for e in ref if e[1]=='SIGNAL')}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "legacy_weekday":
        legacy_weekday_ledger()
    else:
        main()
