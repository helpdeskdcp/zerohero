"""Preview of ZeroHero's rule-based risk module (read-only, records nothing): give the contract and the entry, it prints the stop and the 1R / 2R / 3R targets it would set.
   python scripts/risk_preview.py NIFTY 23100 PE 175 [--expiry 27OCT2026]"""
import os, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from app import sl_detector as D
from app.arena import contracts as CT, submit as SU
a = [x for x in sys.argv[1:] if not x.startswith("--")]
if len(a) != 4: sys.exit(__doc__)
und, strike, ot, entry = a[0].upper(), float(a[1]), a[2].upper(), float(a[3])
if und not in SU.ALLOWED or ot not in ("CE", "PE"): sys.exit(f"underlying must be one of {SU.ALLOWED}, option type CE|PE")
cap = CT.captured(und)["expiries"]; exp = SU.norm_expiry(sys.argv[sys.argv.index("--expiry") + 1]) if "--expiry" in sys.argv else (sorted(cap)[0] if cap else None)
if not exp: sys.exit("no captured expiry for this underlying")
p = D.plan_for(und, exp, strike, ot, entry, time.time())
ok = strike in (cap.get(exp, {}).get("strikes") or [])
print(f"{und} {exp} {strike:g} {ot}  entry {entry:g}  (contract captured: {'yes' if ok else 'NO - stop falls back to 70% of entry'})")
print(f"  stop-loss : {p['sl']:g}   ({p['method']}; swing low {p['swing_low']}, {p['n_quotes_60min']} quotes in the last 60 min)")
print(f"  risk 1R   : {p['risk_per_unit']:g} per unit")
for k, t in zip((1, 2, 3), p["targets"]): print(f"  target 1:{k} : {t:g}")
lp = p["last_price"]
if lp and (entry > 1.5 * lp or entry < 0.6 * lp): print(f"  WARNING: the last captured price is {lp:g}, far from your entry {entry:g}: check the strike / entry.")
print("Rule: stop = clip(0.97 x lowest price of this contract in the last 60 min, 55%..85% of entry); targets at 1R/2R/3R. Official scoring exits at the first target (1:1). Paper only, not a recommendation.")
