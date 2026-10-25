"""Pre-registered: data/research/winners_scalper/SPEC_VIXTRAP_2026-10-25.md"""
import json, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.winners_scalper import backtest as WB
from app.zone_breakout import backtest as ZBB, data as DT
OUT = os.path.join(HERE, "data", "research", "winners_scalper"); ALPHA = 0.025; rng = np.random.default_rng(21)


def vix_series():
    v = pd.read_csv(os.path.join(HERE, "data", "historical", "vix", "india_vix_15m.csv")); v["timestamp"] = pd.to_datetime(v["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata"); return v.set_index("timestamp")["c"]


def trap_flag(sym_d15, vix, trades, lookback=3):
    """Per trade: True if the signal bar is a VIX 'trap' (Pine MODULE 2). VIX aligned to the chart bars by timestamp, then shifted by `lookback` chart bars."""
    ts = pd.DatetimeIndex(sym_d15["timestamp"]).tz_convert("Asia/Kolkata"); al = pd.Series(vix.reindex(ts).ffill().values, index=ts); up = al > al.shift(lookback)
    sig_ts = pd.DatetimeIndex(trades["entry_ts"]).tz_convert("Asia/Kolkata") - pd.Timedelta(minutes=15); u = up.reindex(sig_ts).fillna(False).astype(bool).values; okv = ~pd.isna(al.reindex(sig_ts).values) & ~pd.isna(al.shift(lookback).reindex(sig_ts).values)
    side_buy = (trades["side"] == "BUY").values; return np.where(okv, np.where(side_buy, u, ~u), np.nan)       # BUY & VIX up, or SELL & VIX not up


def block_ci(vals, groups, level=ALPHA, B=4000):
    ug = sorted(set(groups)); gd = {g: vals[groups == g] for g in ug}; bm = [np.concatenate([gd[ug[i]] for i in rng.integers(0, len(ug), len(ug))]).mean() for _ in range(B)]; return float(np.percentile(bm, 100 * level / 2))


def perm_p(vals, k_keep, obs_mean, B=10000):
    return float(np.mean([vals[rng.permutation(len(vals))[:k_keep]].mean() >= obs_mean for _ in range(B)]))


def evaluate(df, col, groups_col, min_n, min_gain, nm):
    kept = df[~df.trap.astype(bool)]; rem = df[df.trap.astype(bool)]; v = df[col].values; kv = kept[col].values
    gain = kv.mean() - v.mean(); lo = block_ci(kv, kept[groups_col].values); p = perm_p(v, len(kv), kv.mean()); ypos = int((kept.groupby("year")[col].mean() > 0).sum()); ny = kept["year"].nunique()
    ok = len(kept) >= min_n and gain >= min_gain and kv.mean() > 0 and lo > 0 and p < ALPHA and (nm != "idx" or ypos >= 3)
    return dict(n_all=int(len(df)), n_kept=int(len(kept)), share_flagged=float(len(rem) / len(df)), mean_all=float(v.mean()), mean_kept=float(kv.mean()), mean_removed=float(rem[col].mean()) if len(rem) else None, gain=float(gain), ci_lo_kept=lo, perm_p=p,
                years_pos=f"{ypos}/{ny}", by_year_kept={int(k): round(float(x), 3) for k, x in kept.groupby("year")[col].mean().items()}, verdict="GO-SHADOW" if ok else "NO-GO")


if __name__ == "__main__":
    vix = vix_series(); res = {}
    for lb in (3, 1, 5):
        fr = []
        for s in DT.INDICES:
            d15 = DT.to_15m(DT.load_5m(s)); t = WB.run(s, 15, "X2"); t = t[pd.DatetimeIndex(t.entry_ts).tz_convert("Asia/Kolkata") >= pd.Timestamp("2022-01-04", tz="Asia/Kolkata")].copy()
            t["trap"] = trap_flag(d15, vix, t, lb); fr.append(t)
        t = pd.concat(fr, ignore_index=True); miss = int(t.trap.isna().sum()); t = t.dropna(subset=["trap"]); t["session"] = t["session"].astype(str)
        r = evaluate(t, "net_R", "session", 300, 0.05, "idx"); r["vix_missing_dropped"] = miss; inv = t.copy(); inv["trap"] = ~inv.trap.astype(bool); r["inverse_filter_mean_kept"] = float(inv[~inv.trap.astype(bool)]["net_R"].mean())
        res[f"index_lb{lb}"] = r; print(f"INDEX lookback {lb}", json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k != "by_year_kept"}), r["by_year_kept"], flush=True)
    for lb in (3,):
        fr = []
        for s in ("NIFTY", "SENSEX"):
            o = pd.read_csv(os.path.join(OUT, f"options_trades_{s}.csv")); o["entry_ts"] = pd.to_datetime(o["entry_ts"], utc=True).dt.tz_convert("Asia/Kolkata"); d15 = DT.to_15m(DT.load_5m(s)); o["trap"] = trap_flag(d15, vix, o, lb); fr.append(o)
        o = pd.concat(fr, ignore_index=True); miss = int(o.trap.isna().sum()); o = o.dropna(subset=["trap"]); r = evaluate(o, "net", "day", 150, 30.0, "opt"); r["vix_missing_dropped"] = miss; res["option_lb3"] = r
        print("OPTIONS lookback 3", json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k != "by_year_kept"}), r["by_year_kept"], flush=True)
    json.dump(res, open(os.path.join(OUT, "results_vixtrap.json"), "w"), indent=1)
