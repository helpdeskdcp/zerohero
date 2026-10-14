"""Builds data/research/hybrid_mp/fixed_summary.json (shown on the dashboard) from the saved raw backtest output -- no numbers typed by hand."""
import json
import re
import statistics

SRC = "data/research/hybrid_mp/fixed_all.txt"
txt = open(SRC).read().splitlines()


def block(start, stop):
    i = next(k for k, l in enumerate(txt) if l.startswith(start))
    j = next((k for k in range(i + 1, len(txt)) if txt[k].startswith(stop)), len(txt))
    return txt[i:j]


prim = block("######## fixed", "######## fixed_abl")
rows = [l.strip() for l in prim if re.match(r"\s+(TRAIN|VALID|OOS) cost=", l)]
reach = [l.strip() for l in prim if "reach T1" in l]
risk = [l.strip() for l in prim if l.strip().startswith("risk bps")]
null = block("######## fixed_null", "exit=")
nm = [float(re.search(r"meanR=([+-][0-9.]+)", l).group(1)) for l in null if "null seed" in l]
real = [float(re.search(r"meanR=([+-][0-9.]+)", l).group(1)) for l in null if l.startswith("REAL OOS")]
smc = block("######## fixed_smc", "######## fixed_other")
out = ["FIXED engine (executable fills, EOD exit, partials + BE after T1, bounded stop, windows, daily limits, MTF state, VWAP/TWAP proxy)",
       "NIFTY 1m data 2017-06..2026-10, chart 5m, round-trip cost shown per row. R = move / planned risk.", ""] + rows + [""] + reach + risk + [""]
out += ["Random-walk null (sign-flipped bars, same engine, gross):",
        f"  OOS real gross meanR {real[0]:+.3f}  vs null mean {statistics.mean(nm):+.3f} (range {min(nm):+.3f}..{max(nm):+.3f}, 8 seeds) -> not distinguishable"
        if real and nm else "  (null not available)", ""]
out += ["Optional SMC confirmation filter (gross; net at 2 bps in last column):"] + [l.strip() for l in smc if "[SMC" in l or "OOS gross" in l][:12]
out += ["", "VERDICT: NO-GO. Corrections remove the script's pathologies and cut the cost drag, but gross edge is ~0 (= null), so net is negative.",
        "Research only: index-level R; options/lot/margin not modelled; cost assumed 2 bps."]
json.dump({"generated_from": SRC, "text": "\n".join(out)}, open("data/research/hybrid_mp/fixed_summary.json", "w"), indent=1)
print("\n".join(out)[:2500])
