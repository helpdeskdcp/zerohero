"""PHASE 1 (read-only): audit of the index candle history used for the hybrid S/R multi-timeframe research.
For NIFTY / BANKNIFTY / SENSEX 1-minute INDEX candles in data/market_history.db (market_candles): coverage, per-day bar counts, gaps, duplicates, stale/flat bars, session alignment, OHLC sanity, sources.
Writes p1_data_audit.json next to this file. Never writes to any database."""
import json, os, sqlite3, sys
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); DB = os.path.join(HERE, "..", "..", "market_history.db")
out = {}
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    raw_dups = c.execute("SELECT COUNT(*) FROM (SELECT bar_start FROM market_candles WHERE symbol=? AND kind='INDEX' AND tf='1m' GROUP BY bar_start HAVING COUNT(*)>1)", (sym,)).fetchone()[0]
    srcs = dict(c.execute("SELECT source, COUNT(*) FROM market_candles WHERE symbol=? AND kind='INDEX' AND tf='1m' GROUP BY source", (sym,)).fetchall())
    d = pd.read_sql_query("SELECT bar_start, o, h, l, c, v FROM market_candles WHERE symbol=? AND kind='INDEX' AND tf='1m' ORDER BY bar_start", c, params=(sym,))
    d["ts"] = pd.to_datetime(d["bar_start"], utc=True).dt.tz_convert("Asia/Kolkata"); d = d.drop_duplicates("ts").set_index("ts").sort_index()
    d["day"] = d.index.date; mod = d.index.hour * 60 + d.index.minute
    per_day = d.groupby("day").size()
    outside = int(((mod < 9 * 60 + 15) | (mod > 15 * 60 + 29)).sum())
    ins = d[(mod >= 9 * 60 + 15) & (mod <= 15 * 60 + 29)]
    # gaps inside a session: consecutive bars of the same day more than 1 minute apart
    g = ins.groupby("day").apply(lambda x: (x.index.to_series().diff().dt.total_seconds().fillna(60) / 60 - 1).clip(lower=0))
    miss = int(g.sum()); longest = int(g.max()) if len(g) else 0
    bad_ohlc = int(((ins.h < ins[["o", "c"]].max(axis=1) - 1e-9) | (ins.l > ins[["o", "c"]].min(axis=1) + 1e-9) | (ins.h < ins.l)).sum())
    flat = (ins.o == ins.h) & (ins.h == ins.l) & (ins.l == ins.c)
    same_close = (ins.c.diff() == 0)
    stale = same_close.groupby(ins["day"]).transform(lambda s: s.rolling(5).sum() == 5)
    full = per_day[(per_day >= 374) & (per_day <= 376)]
    yr = d.groupby(d.index.year).agg(bars=("c", "size"), days=("day", "nunique"))
    out[sym] = {"rows": int(len(d)), "first": str(d.index[0]), "last": str(d.index[-1]), "trading_days": int(per_day.size), "raw_duplicate_timestamps": int(raw_dups), "sources": srcs,
                "days_with_375_bars": int(len(full)), "days_below_360_bars": int((per_day < 360).sum()), "median_bars_per_day": float(per_day.median()),
                "bars_outside_0915_1529": outside, "missing_minutes_inside_sessions": miss, "longest_gap_min": longest, "ohlc_inconsistent_bars": bad_ohlc,
                "flat_ohlc_bars": int(flat.sum()), "bars_in_5plus_unchanged_close_runs": int(stale.sum()), "volume_nonzero_bars": int((d.v > 0).sum()),
                "by_year": {str(k): {"bars": int(v.bars), "days": int(v.days)} for k, v in yr.iterrows()},
                "worst_days": {str(k): int(v) for k, v in per_day.sort_values().head(8).items()}}
    print(sym, json.dumps({k: v for k, v in out[sym].items() if k not in ("by_year", "worst_days")}))
json.dump(out, open(os.path.join(HERE, "p1_data_audit.json"), "w"), indent=1)
