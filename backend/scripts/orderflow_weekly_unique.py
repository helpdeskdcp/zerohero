"""Weekly orderflow win/loss with ONE leg per spike (the leg in the spike candle's direction), i.e. without the BUY+SELL double counting of the first report.
Read-only over captured bars. Usage: python scripts/orderflow_weekly_unique.py 2026-10-08 2026-10-09 ..."""
import collections
import sys

sys.path.insert(0, ".")
from app.orderflow import service  # noqa: E402

days = sys.argv[1:] or ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"]
syms = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL"]
OV = {"NATURALGAS": dict(entry_mode="immediate", sig_filter="candle_dir", max_hold_bars=2, stop_frac=1.0)}
tot = collections.defaultdict(collections.Counter)
pts = collections.defaultdict(float)
nodata = []
for s in syms:
    for d in days:
        try:
            r = service.smart_money(s, d, **OV.get(s, {}))
        except Exception as e:
            nodata.append((s, d)); continue
        if r.get("status") != "OK":
            nodata.append((s, d)); continue
        for sp in r["setups"]:
            c = sp["candle"]
            side = "buy" if c["c"] >= c["o"] else "sell"
            leg = sp.get(side) or {}
            o = leg.get("outcome") or {}
            st = o.get("status")
            if st in (None, "PENDING"):
                continue
            p = o.get("points") or 0.0
            if st == "TIME_EXIT":
                st = "TIME_EXIT_WIN" if p > 0 else "TIME_EXIT_LOSS"
            tot[s][st] += 1; pts[s] += p
print("ORDERFLOW weekly, one leg per spike (spike-candle direction), sessions:", ", ".join(days))
G = collections.Counter()
for s in syms:
    c = tot[s]
    n = sum(c.values())
    if not n:
        print(f"{s:11s} no signals"); continue
    w = c["TARGET_HIT"] + c["TIME_EXIT_WIN"]; l = c["STOP_HIT"] + c["TIME_EXIT_LOSS"]
    G.update(c)
    print(f"{s:11s} signals={n:3d} win={w:3d} loss={l:3d} unresolved={c['TRIGGERED']:3d} | target={c['TARGET_HIT']} stop={c['STOP_HIT']} time+={c['TIME_EXIT_WIN']} time-={c['TIME_EXIT_LOSS']} | points={pts[s]:+.1f}")
w = G["TARGET_HIT"] + G["TIME_EXIT_WIN"]; l = G["STOP_HIT"] + G["TIME_EXIT_LOSS"]
print(f"TOTAL signals={sum(G.values())} win={w} loss={l} unresolved={G['TRIGGERED']} win-rate(resolved)={w/max(w+l,1):.1%}; no data: {nodata}")
