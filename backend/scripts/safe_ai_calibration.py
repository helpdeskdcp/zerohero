"""Calibration of the Safe U-turn marker: how often did the option idea reach its first target (+20 %) before the stop (-25 %) in the 12-month NIFTY back-test (dte 0-2, 0.5 % half-spread, full charges)? Base rates with Wilson 95 %
intervals by thrust bucket, entry time and days-to-expiry, DEV and HOLD-OUT shown separately. This is a BASE RATE for the chart tooltip, not a forecast: the intervals are wide and the net result is break-even (REPLAY_RESULTS.md).
Writes data/research/safe_ai/CALIBRATION.json + CALIBRATION.md.   python scripts/safe_ai_calibration.py"""
import json, math, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.arena.safe_ai import replay_hist as RH


def wilson(k, n, z=1.96):
    if n == 0: return [None, None]
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)); return [round((c - h) / d, 3), round((c + h) / d, 3)]


def cell(label, tr):
    n = len(tr); tgt = sum(1 for t in tr if str(t["why"]).startswith("TARGET")); pos = sum(1 for t in tr if t["net"] > 0); net = sum(t["net"] for t in tr)
    return {"label": label, "n": n, "target_hit": round(tgt / n, 3) if n else None, "target_hit_ci95": wilson(tgt, n), "net_positive": round(pos / n, 3) if n else None, "net_per_trade": round(net / n, 1) if n else None}


idx, opt = RH.load(); tr, sk = RH.simulate(idx, opt, 0.006, 0.005); tr50, _ = RH.simulate(idx, opt, 0.005, 0.005)
def bucket(t): v = t["thrust"]; return "0.60-0.80%" if v < 0.8 else "0.80-1.00%" if v < 1.0 else ">= 1.00%"
def tod(t): h, m = int(t["t"][:2]), int(t["t"][3:]); mins = h * 60 + m; return "09:35-10:30" if mins <= 630 else "10:30-12:30" if mins <= 750 else "12:30-14:30"
out = {"basis": "NIFTY 12 months, dte 0-2, thrust >= 0.60 % (v2), half-spread 0.5 %, stop 25 % / target +20 %, 1 lot", "overall": cell("all", tr), "dev": cell("dev (before 2026-04-01)", [t for t in tr if t["day"] < RH.SPLIT]),
       "hold_out": cell("hold-out (from 2026-04-01)", [t for t in tr if t["day"] >= RH.SPLIT]), "by_thrust": [cell(b, [t for t in tr if bucket(t) == b]) for b in ("0.60-0.80%", "0.80-1.00%", ">= 1.00%")], "excluded_band_0.50_0.60": cell("0.50-0.60% (v1 only, now excluded)", [t for t in tr50 if t["thrust"] < 0.6]),
       "by_time": [cell(b, [t for t in tr if tod(t) == b]) for b in ("09:35-10:30", "10:30-12:30", "12:30-14:30")], "by_dte": [cell(f"dte {d}", [t for t in tr if t["dte"] == d]) for d in (0, 1, 2)], "by_side": [cell(s, [t for t in tr if t["side"] == s]) for s in ("CE", "PE")],
       "note": "Base rate of reaching the first target before the stop. Not a probability forecast; net result is break-even and no cell is statistically different from it."}
d = os.path.join(HERE, "data", "research", "safe_ai"); json.dump(out, open(os.path.join(d, "CALIBRATION.json"), "w"), indent=1)
L = ["# Safe U-turn marker — calibration (base rates)", "", out["basis"], "", "| group | n | target first | 95 % interval | net > 0 | net/trade |", "|---|---|---|---|---|---|"]
for c in [out["overall"], out["dev"], out["hold_out"]] + out["by_thrust"] + [out["excluded_band_0.50_0.60"]] + out["by_time"] + out["by_dte"] + out["by_side"]:
    L.append(f"| {c['label']} | {c['n']} | {c['target_hit']} | {c['target_hit_ci95']} | {c['net_positive']} | {c['net_per_trade']} |")
L += ["", out["note"]]; open(os.path.join(d, "CALIBRATION.md"), "w").write("\n".join(L) + "\n"); print("\n".join(L))
