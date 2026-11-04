"""Exit-rule variants vs the baseline (validate_v1d.json). Gate (unchanged, fixed before the runs): ACCEPT only if on >= 2 of 3 indices OOS net/trade 90% day-cluster lower bound > 0, VALIDATION net/trade > 0 and OOS trades >= 100."""
import json, os
H = os.path.dirname(os.path.abspath(__file__)); names = ["v1d", "X1_be_after_T1", "X2_time_stop_12", "X3_t1_min_0p8", "X4_be_and_time12"]
def load(n):
    p = os.path.join(H, f"validate_{n}.json"); return json.load(open(p))["results"] if os.path.exists(p) else None
base = load("v1d")
for n in names:
    r = load(n)
    if r is None or any("splits" not in r.get(s, {}) or "OOS" not in r[s]["splits"] for s in ("NIFTY", "BANKNIFTY", "SENSEX")): print(n, "NOT FINISHED"); continue
    passes = 0; line = []
    for s in ("NIFTY", "BANKNIFTY", "SENSEX"):
        o, v, d = r[s]["splits"]["OOS"], r[s]["splits"]["VALIDATION"], r[s]["splits"]["DEV"]
        ci = o.get("ci90_net") or [None, None]; ok = (ci[0] is not None and ci[0] > 0) and (v.get("net_per_trade_rs") or -1) > 0 and (o.get("n") or 0) >= 100; passes += ok
        line.append(f"{s[:5]}: DEV {d.get('net_per_trade_rs')} VAL {v.get('net_per_trade_rs')} OOS n={o.get('n')} grossR={o.get('gross_R_mean')} net={o.get('net_per_trade_rs')} ci={ci} pf={o.get('profit_factor_net')} {'PASS' if ok else 'fail'}")
    print(f"{n}: indices passing the gate {passes}/3 -> {'ACCEPT' if passes >= 2 else 'REJECT'}"); [print("   ", x) for x in line]
