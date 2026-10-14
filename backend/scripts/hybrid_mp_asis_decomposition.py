"""Why is the literal-Pine execution (close fill, level exits, overnight holds) so much better gross than the executable version?
Step from AS-PLOTTED to REALISTIC one assumption at a time on NIFTY 5m, Pine default config, PINE exit (SL or T4). Same steps on a sign-flip null."""
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import Params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402
from app.research_engines.smart_bos.sim import summarize  # noqa: E402
from app.research_engines.smart_bos.splits import OOS, TRAIN, VALID, of_split  # noqa: E402

VARS = [("AS-PLOTTED", dict(fill="close", gap_fills=False, eod_flat=False)),
        ("+ next-open fill only", dict(fill="next_open", gap_fills=False, eod_flat=False)),
        ("+ gap fills only", dict(fill="close", gap_fills=True, eod_flat=False)),
        ("+ EOD flat only (no overnight)", dict(fill="close", gap_fills=False, eod_flat=True)),
        ("+ next-open + EOD flat", dict(fill="next_open", gap_fills=False, eod_flat=True)),
        ("REALISTIC (all three)", dict(fill="next_open", gap_fills=True, eod_flat=True))]


def table(chart, label, split=True):
    print(f"\n{label}")
    for name, kw in VARS:
        tr, st = run(chart, Params(chart_tf=5, exit_model="PINE", **kw))
        if split:
            cells = []
            for sp_name, sp in (("TRAIN", TRAIN), ("VALID", VALID), ("OOS", OOS)):
                s = summarize(of_split(tr, sp), cost_bps=0, boot=300)
                cells.append(f"{sp_name} n={s['n']:5d} {s['mean_r']:+.3f}({s['ci95_r'][0]:+.2f},{s['ci95_r'][1]:+.2f})")
            on = sum(1 for t in tr if t.overnight_days > 0) / max(len(tr), 1)
            print(f"  {name:32s} " + " | ".join(cells) + f" | overnight {on:.0%}")
        else:
            s = summarize(tr, cost_bps=0, boot=300)
            print(f"  {name:32s} n={s['n']:5d} gross meanR {s['mean_r']:+.3f} ({s['ci95_r'][0]:+.2f},{s['ci95_r'][1]:+.2f})")


b1 = load_1m("NIFTY", "INDEX")
chart = resample(b1, 5, 555, 930)
table(chart, "REAL NIFTY 5m, Pine defaults (gross meanR, 95% CI)")
days = (b1["t"] + 19800) // 86400
sel = days >= (__import__("datetime").date(2024, 1, 1).toordinal() - 719163)
b = {k: v[sel] for k, v in b1.items()}
for seed in range(3):
    rng = np.random.default_rng(seed)
    pc = np.r_[b["c"][0], b["c"][:-1]]
    ln = {k_: np.log(b[k_] / pc) for k_ in ("o", "h", "l", "c")}
    flip = rng.random(len(pc)) < 0.5
    no = np.where(flip, -ln["o"], ln["o"]); nc = np.where(flip, -ln["c"], ln["c"])
    nh = np.where(flip, -ln["l"], ln["h"]); nl = np.where(flip, -ln["h"], ln["l"])
    chain = np.exp(np.cumsum(nc)) * b["c"][0]
    npc = np.r_[b["c"][0], chain[:-1]]
    sb = {"t": b["t"], "o": npc * np.exp(no), "h": npc * np.exp(nh), "l": npc * np.exp(nl), "c": chain, "v": b["v"]}
    table(resample(sb, 5, 555, 930), f"SIGN-FLIP NULL seed {seed} (2024-01.. subset; destroys drift/autocorrelation, keeps volatility)", split=False)
