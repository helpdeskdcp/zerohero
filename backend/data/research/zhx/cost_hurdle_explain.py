import json, os, sys
import numpy as np
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cost_hurdle.py")).read().split("base = pooled(0.0)")[0].replace('print("friction', '_ = ("friction'))
for s in SPL:
    rows = src[s]["trade_rows"]; ratio = np.array([x["risk"] / FR[s] for x in rows]); print(s, "risk/friction: p10 %.1f median %.1f p90 %.1f  (share below 3x: %.1f%%)" % (np.percentile(ratio, 10), np.median(ratio), np.percentile(ratio, 90), 100 * np.mean(ratio < 3)))
    for lo, hi in ((0, 6), (6, 10), (10, 20), (20, 1e9)):
        sel = [x for x, r in zip(rows, ratio) if lo <= r < hi]
        if len(sel) >= 30: r = summ(sel, s); print("   ratio %g-%g: n=%d grossR=%s net/trade=%s" % (lo, hi if hi < 1e8 else 999, r["n"], r["gross_R"], r["net"]))
