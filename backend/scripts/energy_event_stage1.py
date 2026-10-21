"""Stage-1 premise test of the MCX energy-event volatility hypothesis. Spec (committed first): data/research/energy_event/SPEC_2026-10-21.md  -- read-only on market_history.db."""
import json, os, sqlite3, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import numpy as np
from scipy.stats import mannwhitneyu
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = timedelta(hours=5, minutes=30)
UNCERTAIN = {"2026-09-09", "2026-09-10", "2026-10-14", "2026-10-15"}
EVENT_DOW = {"CRUDEOIL": 2, "NATURALGAS": 3}                     # Mon=0: Wed, Thu
rng = np.random.default_rng(7)
db = sqlite3.connect(f"file:{os.path.join(HERE, 'data', 'market_history.db')}?mode=ro", uri=True, timeout=60)


def bars(sym):
    out = defaultdict(list); multi = set()
    for src, lo, hi in (("DHAN_HISTORICAL", "2026-06-30", "2026-08-31"), ("ANGELONE_CANDLES", "2026-09-01", "2026-10-20")):
        rows = db.execute("SELECT bar_start,session_date_ist,o,h,l,c,expiry FROM market_candles WHERE symbol=? AND kind='FUTURE' AND tf='1m' AND source=? AND session_date_ist>=? AND session_date_ist<=?", (sym, src, lo, hi)).fetchall()
        byd = defaultdict(lambda: defaultdict(list))
        for bs, d, o, h, l, c, ex in rows: byd[d][ex].append((bs, o, h, l, c))
        for d, ex in byd.items():
            if len(ex) > 1: multi.add(d)
            best = max(ex.values(), key=len)
            out[d] = sorted(best)
    return out, multi


def hhmm(bs):
    t = datetime.fromisoformat(bs.replace("Z", "+00:00")) + IST
    return t.hour * 60 + t.minute


def rr(bl, a, b):
    sel = [x for x in bl if a <= hhmm(x[0]) < b]
    if len(sel) < 12: return None
    o = sel[0][1]; return (max(x[2] for x in sel) - min(x[3] for x in sel)) / o if o else None


def ratio_ci(ev, ct, n=5000):
    r = np.median(ev) / np.median(ct)
    bs = [np.median(rng.choice(ev, len(ev))) / np.median(rng.choice(ct, len(ct))) for _ in range(n)]
    return float(r), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


res = {"spec": "SPEC_2026-10-21.md"}
for sym, edow in EVENT_DOW.items():
    bl, multi = bars(sym); ev, ct, evr, ctr, uncertain, nodata = [], [], [], [], {}, []
    per = {"A": ([], []), "B": ([], [])}
    for d, b in sorted(bl.items()):
        dow = datetime.strptime(d, "%Y-%m-%d").weekday()
        w, base = rr(b, 20 * 60, 20 * 60 + 15), rr(b, 19 * 60 + 30, 19 * 60 + 45)
        if w is None or base is None: nodata.append(d); continue
        if d in UNCERTAIN: uncertain[d] = {"W": w, "B": base}; continue
        part = "A" if d <= "2026-08-31" else "B"
        if dow == edow:
            ev.append(w); per[part][0].append(w)
            if base > 0: evr.append(w / base)
        elif dow in (0, 1, 4):
            ct.append(w); per[part][1].append(w)
            if base > 0: ctr.append(w / base)
    out = {"n_event": len(ev), "n_control": len(ct), "median_W_event": float(np.median(ev)) if ev else None, "median_W_control": float(np.median(ct)) if ct else None,
           "dates_multi_contract": sorted(multi), "dates_no_window_data": nodata, "uncertain_dates_reported_separately": uncertain}
    if len(ev) >= 12 and len(ct) >= 12:
        r, lo, hi = ratio_ci(np.array(ev), np.array(ct)); p = float(mannwhitneyu(ev, ct, alternative="greater").pvalue)
        rA = float(np.median(per["A"][0]) / np.median(per["A"][1])) if per["A"][0] and per["A"][1] else None
        rB = float(np.median(per["B"][0]) / np.median(per["B"][1])) if per["B"][0] and per["B"][1] else None
        rel = float(np.median(evr) / np.median(ctr)) if evr and ctr else None
        out.update(ratio=r, ci95=[lo, hi], mannwhitney_p_one_sided=p, ratio_period_A_jun_aug=rA, ratio_period_B_sep_oct=rB, n_event_A=len(per["A"][0]), n_event_B=len(per["B"][0]), relative_expansion_ratio=rel,
                   checks={"ratio>=1.5": r >= 1.5, "ci_lo>1.15": lo > 1.15, "p<0.01": p < 0.01, "both_periods>1.2": bool(rA and rB and rA > 1.2 and rB > 1.2)})
        out["premise_holds"] = all(out["checks"].values())
    else: out["premise_holds"] = "INSUFFICIENT"
    # long-sample daily check (context only)
    drows = db.execute("SELECT session_date_ist,o,h,l FROM market_candles WHERE symbol=? AND kind='FUTURE' AND tf='1d' AND source='DHAN_HISTORICAL' ORDER BY session_date_ist", (sym,)).fetchall()
    dev, dct = [], []
    for d, o, h, l in drows:
        if not o: continue
        dow = datetime.strptime(d, "%Y-%m-%d").weekday(); v = (h - l) / o
        (dev if dow == edow else dct if dow in (0, 1, 4) else []).append(v)
    if dev and dct:
        r, lo, hi = ratio_ci(np.array(dev), np.array(dct), 3000); out["daily_5y_context"] = {"n_event_dow": len(dev), "n_control": len(dct), "range_ratio": r, "ci95": [lo, hi]}
    res[sym] = out
d = os.path.join(HERE, "data", "research", "energy_event"); os.makedirs(d, exist_ok=True)
json.dump(res, open(os.path.join(d, "STAGE1_RESULT_2026-10-21.json"), "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
