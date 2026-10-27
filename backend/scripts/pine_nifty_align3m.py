"""Engine alignment on real data: every 5-minute step the 3-minute chart sees must equal the 5-minute chart's own output for the same 5-minute bar, and must come from a
bar that had already CLOSED when the 3-minute bar opened. usage: pine_nifty_align3m.py run_3m.json run_5m.json"""
import json, math, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(HERE, "scripts"))
import pine_nifty_harness as H
def load(p):
    d = json.load(open(p)); return d, H.unpack({q["title"]: [float("nan") if v is None else float(v) for v in q["values"]] for q in d["plots"]})
d3, p3 = load(sys.argv[1]); d5, p5 = load(sys.argv[2])
idx5 = {int(t): i for i, t in enumerate(d5["times"])}
keys = ["Ref ATR", "Ref RSI", "Ref swing high", "Ref swing low", "Ref orH", "Ref orL", "Gap scenario code", "Scenario validated", "Gap points", "Daily ATR", "Gap / daily ATR", "Engine 5m close",
        "Event bits", "Open bull FVG", "Open bear FVG", "Stress flags (1 bull, 2 bear)", "Stress zone slots", "Bar of day"]
steps = [i for i in range(len(d3["times"])) if p3["New 5m step"][i] == 1]
bad = {}; partial = 0; checked = 0; missing = 0
t0 = d3["times"][0] + 20 * 86400000                                   # skip the first 20 days: the 5-minute reference run may have started earlier (indicator warm-up differs)
steps = [i for i in steps if d3["times"][i] >= t0]
for i in steps:
    T = int(round(p3["Engine 5m bar time"][i] * 1000)); j = idx5.get(T)
    if j is None: missing += 1; continue
    if d3["times"][i] < T + 300000: partial += 1
    checked += 1
    for k in keys:
        a, b = p3[k][i], p5[k][j]
        if not ((math.isnan(a) and math.isnan(b)) or abs(a - b) <= 1e-9 * max(1, abs(a), abs(b))): bad.setdefault(k, []).append((i, a, b))
print(json.dumps({"host_bars": len(d3["times"]), "5m_bars": len(d5["times"]), "steps": len(steps), "checked": checked, "step_not_in_5m_run": missing, "steps_from_a_partial_5m_bar": partial,
                  "series_checked": len(keys), "mismatching_series": {k: (len(v), v[:2]) for k, v in bad.items()}}, indent=1, default=str))
