"""H-IDX premise test (spec: data/research/index_event/SPEC_2026-10-21.md). Read-only."""
import json, os, sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np
from scipy.stats import wilcoxon
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = timedelta(hours=5, minutes=30); rng = np.random.default_rng(7)
meta = json.load(open(os.path.join(HERE, "app", "hist_options", "data", "expiry_metadata_upstox.json")))
db = sqlite3.connect(f"file:{os.path.join(HERE, 'data', 'market_history.db')}?mode=ro", uri=True, timeout=60)
res = {"spec": "SPEC_2026-10-21.md", "alpha": 0.01 / 3}
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    byd = defaultdict(list)
    for bs, d, o, h, l in db.execute("SELECT bar_start,session_date_ist,o,h,l FROM market_candles WHERE symbol=? AND kind='INDEX' AND tf='1m' AND source='DHAN_HISTORICAL' AND session_date_ist>='2024-09-01' AND session_date_ist<='2026-09-28'", (sym,)):
        t = datetime.fromisoformat(bs.replace("Z", "+00:00")) + IST; m = t.hour * 60 + t.minute
        if 14 * 60 + 30 <= m < 15 * 60 + 15: byd[d].append((m, o, h, l))
    rr = {}
    for d, b in byd.items():
        if len(b) >= 40: b.sort(); rr[d] = (max(x[2] for x in b) - min(x[3] for x in b)) / b[0][1]
    days = sorted(rr); exp = {x for x in meta[sym] if "2024-10-01" <= x <= "2026-09-28"}
    ratios, dates, skipped = [], [], []
    for e in sorted(exp):
        if e not in rr: skipped.append((e, "no/short window data")); continue
        prior = [rr[d] for d in days if d < e and d not in exp][-20:]
        if len(prior) < 10: skipped.append((e, "baseline < 10 days")); continue
        ratios.append(rr[e] / float(np.median(prior))); dates.append(e)
    out = {"n_listed_expiries": len(exp), "n_used": len(ratios), "skipped": skipped}
    if len(ratios) >= 30:
        r = np.array(ratios); med = float(np.median(r)); bs = [np.median(rng.choice(r, len(r))) for _ in range(5000)]
        p = float(wilcoxon(np.log(r), alternative="greater").pvalue); half = len(r) // 2
        mA, mB = float(np.median(r[:half])), float(np.median(r[half:]))
        out.update(median_ratio=med, ci95=[float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))], wilcoxon_p_one_sided=p, median_first_half=mA, median_second_half=mB, first_date=dates[0], last_date=dates[-1],
                   share_ratio_gt1=float((r > 1).mean()),
                   checks={"median>=1.30": med >= 1.30, "ci_lo>1.10": out_ci if False else float(np.percentile(bs, 2.5)) > 1.10, "p<0.0033": p < 0.01 / 3, "both_halves>1.15": mA > 1.15 and mB > 1.15})
        out["premise_holds"] = all(out["checks"].values())
    else: out["premise_holds"] = "INSUFFICIENT"
    res[sym] = out
d = os.path.join(HERE, "data", "research", "index_event"); json.dump(res, open(os.path.join(d, "STAGE1_RESULT_2026-10-21.json"), "w"), indent=1, default=str)
print(json.dumps({k: ({kk: vv for kk, vv in v.items() if kk != "skipped"} | {"n_skipped": len(v["skipped"])}) if isinstance(v, dict) and "skipped" in v else v for k, v in res.items()}, indent=1, default=str))
