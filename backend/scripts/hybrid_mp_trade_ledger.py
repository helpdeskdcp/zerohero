"""Trade ledger export (NIFTY 5m): FIXED primary and PINE_ASIS (the script's own execution). One row per trade, with planned levels."""
import csv
import datetime as dt
import json
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import Params, fixed_params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402
from app.research_engines.smart_bos.splits import OOS, TRAIN, VALID  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
b1 = load_1m("NIFTY", "INDEX")
chart = resample(b1, 5, 555, 930)
fmt = lambda i: dt.datetime.fromtimestamp(int(chart["t"][i]), IST).strftime("%Y-%m-%d %H:%M")
split = lambda day: "TRAIN" if TRAIN[0].toordinal() - 719163 <= day <= TRAIN[1].toordinal() - 719163 else ("VALID" if VALID[0].toordinal() - 719163 <= day <= VALID[1].toordinal() - 719163 else "OOS")
rows, n = [], 0
for variant, p in (("FIXED_primary_5m", fixed_params(chart_tf=5)),
                   ("PINE_ASIS_5m", Params(chart_tf=5, fill="close", gap_fills=False, eod_flat=False, exit_model="PINE"))):
    tr, _ = run(chart, p, b1=b1)
    for t in tr:
        n += 1
        base = float(chart["c"][t.sig_i])
        lv = [base + t.side * t.risk * k for k in (1, 2, 3, 4)]
        cost_r = 2.0 * t.entry / 1e4 / t.risk
        rows.append([n, variant, "NIFTY", 5, split(t.day), "BUY" if t.side == 1 else "SELL", t.kind, fmt(t.sig_i), fmt(t.entry_i), fmt(t.exit_i),
                     round(t.entry, 2), round(base - t.side * t.risk, 2), *[round(x, 2) for x in lv], round(t.risk, 2), round(1e4 * t.risk / t.entry, 1),
                     t.reason, t.max_t, round(t.exit, 2), round(t.pnl, 3), round(t.r, 4), round(t.r - cost_r, 4), round(t.mae, 2), round(t.mfe, 2),
                     t.overnight_days, json.dumps([[round(w, 3), round(px, 2)] for w, px in t.parts])])
out = "data/research/hybrid_mp/audit/TRADE_LEDGER.csv"
with open(out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["trade_id", "variant", "symbol", "chart_tf", "split", "side", "entry_type", "signal_bar_ist", "entry_bar_ist", "exit_bar_ist", "entry_price",
                "planned_sl", "T1", "T2", "T3", "T4", "risk_points", "risk_bps", "exit_reason", "max_target", "exit_price_last_part", "pnl_points",
                "r_gross", "r_net_2bps", "mae_points", "mfe_points", "overnight_days", "parts_weight_price"])
    w.writerows(rows)
print(len(rows), "trades ->", out)
