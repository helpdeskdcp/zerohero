"""Zone Breakout all-index back-test exactly as pre-registered (data/research/zone_breakout/SPEC_2026-10-25.md). Writes results.json.   python scripts/zone_breakout_backtest.py"""
import json, os, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from app.zone_breakout import backtest as BT, data as DT, strategy as ZB

VARIANTS = {"A": ("next_open", True), "B": ("close", True), "C": ("close", False)}


def main():
    t0 = time.time(); bars = {s: DT.load_5m(s) for s in DT.INDICES}
    print({s: (len(b), str(b["timestamp"].iloc[0])[:10], str(b["timestamp"].iloc[-1])[:10]) for s, b in bars.items()}, round(time.time() - t0), "s", flush=True)
    res = {"data": {s: {"bars": int(len(b)), "from": str(b["timestamp"].iloc[0])[:10], "to": str(b["timestamp"].iloc[-1])[:10]} for s, b in bars.items()}, "cells": {}, "verdict": {}}
    allt = {}
    for var, (entry, intraday) in VARIANTS.items():
        for tf in (5, 15):
            frames = []
            for s in DT.INDICES:
                t = BT.run(s, tf, ZB.Config(), entry, intraday, bars=bars[s]); frames.append(t)
                for per in BT.PERIODS: res["cells"][f"{var}|{tf}|{s}|{per}"] = BT.summary(BT.period(t, per))
                res["cells"][f"{var}|{tf}|{s}|ALL"] = BT.summary(t)
            tt = pd.concat([f for f in frames if len(f)], ignore_index=True); allt[(var, tf)] = tt
            for per in BT.PERIODS: res["cells"][f"{var}|{tf}|POOLED|{per}"] = BT.summary(BT.period(tt, per))
            print(var, tf, "done", round(time.time() - t0), "s;", len(tt), "trades", flush=True)
    for tf in (5, 15):
        ok = True; enough = True
        for per in ("P1", "P2"):
            p = res["cells"][f"A|{tf}|POOLED|{per}"]; enough &= p["n"] >= 300; ok &= bool(p["n"] >= 300 and p["mean_R_net"] > 0 and p.get("ci_lo", -1) > 0)
            for s in ("NIFTY", "BANKNIFTY"): ok &= bool(res["cells"][f"A|{tf}|{s}|{per}"].get("mean_R_net", -1) > 0)
        res["verdict"][f"{tf}m"] = "INSUFFICIENT_SAMPLE" if not enough else ("GO-SHADOW" if ok else "NO-GO")
    json.dump(res, open(os.path.join(HERE, "data", "research", "zone_breakout", "results.json"), "w"), indent=1, default=float)
    print("VERDICT", res["verdict"])
    print(f"{'var':4}{'tf':>3} {'scope':14}{'per':5}{'n':>6}{'win%':>6}{'grossR':>8}{'netR':>8}{'CIlo':>8}{'PF':>6}{'medRiskbp':>10}{'<5bp%':>6}")
    for var in VARIANTS:
        for tf in (5, 15):
            for scope in ("POOLED", "NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY", "BANKEX"):
                for per in ("P1", "P2", "P3"):
                    c = res["cells"][f"{var}|{tf}|{scope}|{per}"]
                    if c["n"] >= 30 and (scope == "POOLED" or var == "A"): print(f"{var:4}{tf:>3} {scope:14}{per:5}{c['n']:6d}{100 * c['win_rate']:6.1f}{c['mean_R_gross']:8.3f}{c['mean_R_net']:8.3f}{c.get('ci_lo', float('nan')):8.3f}{c['profit_factor_net']:6.2f}{c['median_risk_bp']:10.1f}{100 * c['share_risk_lt5bp']:6.0f}")


if __name__ == "__main__":
    main()
