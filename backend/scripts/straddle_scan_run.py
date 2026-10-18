"""Expiry-structure scan on NIFTY weekly options (Upstox expired-contract 5m bars, ATM +/-5 strikes, 60 expiries 2025-08..2026-10) with the 12 pre-registered
hypotheses of app/strategy_lab/straddle_scan.py. READ-ONLY. One observation per expiry; net of the explicit cost model; Benjamini-Hochberg across the 12.
Usage: python scripts/straddle_scan_run.py"""
import csv
import datetime as dt
import sqlite3
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import resample  # noqa: E402
from app.research_engines.smart_bos.data import ist_minutes, load_1m  # noqa: E402
from app.strategy_lab import edge_scan as ES  # noqa: E402
from app.strategy_lab import straddle_scan as SS  # noqa: E402

DB = "data/historical/upstox/upstox_research.db"
LINES = []


def say(s=""):
    print(s)
    LINES.append(s)


# ---- spot at each 5m bar open (our own NIFTY series; independent of the option source)
b1 = load_1m("NIFTY", "INDEX", start="2025-06-01", end="2027-01-01", prefer="ANGELONE_CANDLES")
ch = resample(b1, 5, 555, 930)
days, mod = ist_minutes(ch["t"])
SPOT = {}
for d, m, o in zip(days, mod, ch["o"]):
    SPOT[((dt.date(1970, 1, 1) + dt.timedelta(days=int(d))).isoformat(), f"{int(m) // 60:02d}:{int(m) % 60:02d}")] = float(o)
sessions = sorted({k[0] for k in SPOT})
def spot_at(day, t, tol_min=10):
    """index price at the bar open of `t`, else the nearest available 5m bar within +-tol_min minutes (index data has short outages); None beyond that."""
    v = SPOT.get((day, t))
    if v is not None:
        return v
    for dm in range(5, tol_min + 1, 5):
        for sgn in (1, -1):
            hh, mm = int(t[:2]), int(t[3:])
            tt = hh * 60 + mm + sgn * dm
            v = SPOT.get((day, f"{tt // 60:02d}:{tt % 60:02d}"))
            if v is not None:
                return v
    return None

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
expiries = [r[0] for r in con.execute("SELECT DISTINCT expiry FROM normalized_option_bars WHERE underlying='NIFTY' ORDER BY expiry")]
say(f"NIFTY weekly expiries with option bars: {len(expiries)} ({expiries[0]} .. {expiries[-1]}); spot from our own 5m series; cost model: flat brokerage + STT on sold premium + txn/GST + {SS.SLIP} pt slippage per leg per transaction")
MODE = sys.argv[1] if len(sys.argv) > 1 else "carry"          # carry = last traded price if a leg did not trade in the final 5 min (counted); strict = drop that trade
assert MODE in ("carry", "strict")
res = {h.name: [] for h in SS.HYPS}
dropped = {h.name: [] for h in SS.HYPS}
rows_out = []
used = skipped = 0
for e in expiries:
    if e not in sessions:
        skipped += 1
        continue
    i = sessions.index(e)
    prev = sessions[i - 1] if i > 0 else None
    bars = {}
    q = ("SELECT strike, option_type, replace(substr(timestamp,1,16),'T',' '), open, high, low, close FROM normalized_option_bars "
         "WHERE underlying='NIFTY' AND expiry=? AND (substr(timestamp,1,10)=? OR substr(timestamp,1,10)=?)")
    for k, ty, ts, o, h, l, c in con.execute(q, (e, e, prev or e)):
        bars.setdefault((float(k), ty), {})[ts] = (o, h, l, c)
    bars = {(int(k), ty): v for (k, ty), v in bars.items()}
    used += 1
    for h in SS.HYPS:
        r = SS.trade(h, e, prev, bars, spot_at, carry_exit=(MODE == "carry"))
        if r is None:
            dropped[h.name].append(e)
        if r:
            res[h.name].append((e, r))
            rows_out.append([e, h.name, round(r["gross"], 2), round(r["cost"], 2), round(r["net"], 2), round(r["net_bps"], 2), r["atm"], round(r["entry_prem"], 2), round(r["exit_prem"], 2)])
say(f"mode: {MODE}; expiries used {used}, skipped (not a session in our index data) {skipped}")

say(f"\n{'hypothesis':36s} {'n':>3s} {'gross':>7s} {'cost':>5s} {'net pts':>8s} {'net bps':>8s} {'win%':>5s} {'t':>6s} {'p(>0)':>7s} | {'5% worst':>8s} {'worst':>7s} {'best':>7s} | halves net")
pv, names = [], []
for h in SS.HYPS:
    v = res[h.name]
    net = [r["net"] for _, r in v]
    mu, t, p = SS.tstat_one_sided(net)
    pv.append(p); names.append(h.name)
    if not net:
        say(f"{h.name:36s}   0 (no data)")
        continue
    nd = len(dropped[h.name])
    g = np.mean([r["gross"] for _, r in v]); c = np.mean([r["cost"] for _, r in v])
    half = len(net) // 2
    say(f"{h.name:36s} {len(net):3d} {g:7.1f} {c:5.1f} {mu:8.2f} {np.mean([r['net_bps'] for _, r in v]):8.2f} {np.mean(np.array(net) > 0) * 100:5.0f} {t:6.2f} {p:7.4f} | "
        f"{np.percentile(net, 5):8.1f} {min(net):7.1f} {max(net):7.1f} | {np.mean(net[:half]):+.1f} / {np.mean(net[half:]):+.1f} | dropped {nd}, carried legs {sum(r['carried_legs'] for _, r in v)}")
rej = ES.bh(pv, 0.10)
surv = [n for n, r in zip(names, rej) if r]
say(f"\nBH-FDR (12 hypotheses, q=0.10, one-sided net>0): survivors = {surv if surv else 'NONE'}")
say("n is about 55-60 expiries, i.e. far below the promotion gate (n>=200 trades, >=40 days with a stable out-of-sample): even a survivor here is only a FORWARD-TEST candidate, never a trade.")
say("Short-premium results must also be read against the tail column: a high win rate with one worst loss several times the average win is the signature of selling insurance.")
open(f"data/research/strategy_lab/straddle_scan_2026-10-18_{MODE}.txt", "w").write("\n".join(LINES) + "\n")
with open(f"data/research/strategy_lab/straddle_scan_2026-10-18_{MODE}_trades.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["expiry", "hypothesis", "gross_pts", "cost_pts", "net_pts", "net_bps", "atm", "entry_prem", "exit_prem"]); w.writerows(rows_out)
