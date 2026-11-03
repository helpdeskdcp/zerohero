"""ZHX ablation study (pre-registered). Same trades as the baseline validation (validate_v1d.json); each ENGINE is a causal feature computed on 5m bars up to the signal bar, used only to keep/skip a trade.
Variants (fixed before any result): A_trend ADX14>=20 | A_range ADX14<20 | V_hi ATR14 above its 100-bar median | V_lo below | P_body signal-bar body >= 1.0 ATR | P_close signal bar closes in outer 25% | T_ema close on the trade side of EMA50 (5m)
| T_h1 1h trend (C>EMA20>EMA50, 12 five-minute bars x 12 smoothing) not against | M_rsi RSI14 on the trade side of 50 | M_ex RSI14 not exhausted (long<70, short>30) | S_score score >= 7 | S_win entry 09:35-10:30.
SELECTION RULE (written before running): a variant is KEPT only if pooled DEV+VALIDATION (all three indices) net per trade improves by >= Rs30 vs baseline AND keeps >= 150 trades. KEPT variants are combined (AND). OOS is looked at once, afterwards.
Gate (unchanged) for ACCEPT: on >=2 of 3 indices OOS net/trade 90% day-cluster lower bound > 0, VALIDATION net/trade > 0, OOS trades >= 100."""
import json, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.research_engines.hybrid_sr_mtf import data as D, metrics as M
src = json.load(open(os.path.join(HERE, "validate_v1d.json")))
SPL = {"NIFTY": {"DEV": (20170601, 20211231), "VALIDATION": (20220101, 20231231), "OOS": (20240101, 20261031)}, "BANKNIFTY": {"DEV": (20210929, 20221231), "VALIDATION": (20230101, 20231231), "OOS": (20240101, 20261031)},
       "SENSEX": {"DEV": (20210929, 20221231), "VALIDATION": (20230101, 20231231), "OOS": (20240101, 20261031)}}
def wilder(x, n): return x.ewm(alpha=1 / n, adjust=False).mean()
def feats(sym):
    d = D.load_index_1m(sym); b = d.resample("5min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(); b = b[b.index.hour * 60 + b.index.minute >= 9 * 60 + 15]
    h, l, c, o = b.high, b.low, b.close, b.open; pc = c.shift(1); tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1); atr = wilder(tr, 14)
    up = h.diff(); dn = -l.diff(); pdm = ((up > dn) & (up > 0)) * up; mdm = ((dn > up) & (dn > 0)) * dn
    pdi = 100 * wilder(pdm, 14) / atr; mdi = 100 * wilder(mdm, 14) / atr; adx = wilder(100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan), 14)
    dl = c.diff(); rsi = 100 - 100 / (1 + wilder(dl.clip(lower=0), 14) / wilder((-dl).clip(lower=0), 14).replace(0, np.nan))
    e50 = c.ewm(span=50, adjust=False).mean(); e20 = c.ewm(span=20, adjust=False).mean()
    h1e20 = c.ewm(span=20 * 12, adjust=False).mean(); h1e50 = c.ewm(span=50 * 12, adjust=False).mean()
    F = pd.DataFrame({"adx": adx, "atr": atr, "atrmed": atr.rolling(100, min_periods=30).median(), "body": (c - o).abs() / atr, "loc": (c - l) / (h - l).replace(0, np.nan), "rsi": rsi, "c": c, "e50": e50, "h1bull": (c > h1e20) & (h1e20 > h1e50), "h1bear": (c < h1e20) & (h1e20 < h1e50)})
    return F, b
VAR = {"A_trend": lambda f, s: f.adx >= 20, "A_range": lambda f, s: f.adx < 20, "V_hi": lambda f, s: f.atr > f.atrmed, "V_lo": lambda f, s: f.atr <= f.atrmed, "P_body": lambda f, s: f.body >= 1.0,
       "P_close": lambda f, s: (f["loc"] >= 0.75) if s > 0 else (f["loc"] <= 0.25), "T_ema": lambda f, s: (f.c > f.e50) if s > 0 else (f.c < f.e50), "T_h1": lambda f, s: not (f.h1bear if s > 0 else f.h1bull),
       "M_rsi": lambda f, s: (f.rsi > 50) if s > 0 else (f.rsi < 50), "M_ex": lambda f, s: (f.rsi < 70) if s > 0 else (f.rsi > 30), "S_score": lambda f, s: f.score >= 7.0, "S_win": lambda f, s: 575 <= f.minute <= 630}
rows_all = {}
for sym, r in src["results"].items():
    F, b = feats(sym); rows = r["trade_rows"]; out = []
    for x in rows:
        t = pd.Timestamp(x["t_entry"], unit="s", tz="UTC").tz_convert("Asia/Kolkata"); k = F.index.searchsorted(t, side="right") - 1
        if k < 0: continue
        f = F.iloc[k].copy(); f["score"] = x["score"]; f["minute"] = t.hour * 60 + t.minute; x["_f"] = f; out.append(x)
    rows_all[sym] = out
def ok(x, name): 
    try: v = VAR[name](x["_f"], x["side"]); return bool(v) if v == v else False
    except Exception: return False
def summ(sel, sym):
    if not sel: return {"n": 0}
    rr = [{"day": x["day"], "side": x["side"], "R": x["R"], "pts": x["pts"], "risk": x["risk"], "score": x["score"], "t_entry": x["t_entry"], "reason": "", "ambiguous": False, "mae": 0.0, "mfe": 0.0, "hit_t1": False, "hit_t2": False, "hit_t3": False} for x in sel]
    s = M.summarize(rr, sym); return {"n": s.get("n"), "gross_R": round(float(np.mean([x["R"] for x in sel])), 3), "net": s.get("net_per_trade_rs"), "ci90": s.get("ci90_per_trade"), "pf": s.get("profit_factor")}
def split(rows, sym, sp, pred): a, z = SPL[sym][sp]; return [x for x in rows if a <= x["day"] <= z and pred(x)]
def pooled_net(pred, sps):                                             # mean net per trade over all trades of the three indices in the given splits (each trade valued by its own index conversion)
    tot, n = 0.0, 0
    for sym in rows_all:
        for sp in sps:
            s = summ(split(rows_all[sym], sym, sp, pred), sym)
            if s["n"]: tot += (s["net"] or 0) * s["n"]; n += s["n"]
    return (tot / n if n else None), n
base = pooled_net(lambda x: True, ("DEV", "VALIDATION")); res = {"baseline_dev_val": base, "variants": {}}; kept = []
print("baseline DEV+VAL pooled net/trade, n:", base)
for name in VAR:
    v = pooled_net(lambda x, nm=name: ok(x, nm), ("DEV", "VALIDATION")); keep = v[0] is not None and v[1] >= 150 and v[0] - base[0] >= 30
    res["variants"][name] = {"dev_val_net": v[0], "n": v[1], "delta": (v[0] - base[0]) if v[0] is not None else None, "kept": keep}; print(name, v, "KEPT" if keep else "-")
    if keep: kept.append(name)
res["kept"] = kept; comb = (lambda x: all(ok(x, nm) for nm in kept)) if kept else (lambda x: True)
res["oos"] = {}
for label, pred in (("BASELINE", lambda x: True), ("COMBINED", comb)):
    res["oos"][label] = {}
    for sym in rows_all:
        res["oos"][label][sym] = {sp: summ(split(rows_all[sym], sym, sp, pred), sym) for sp in ("DEV", "VALIDATION", "OOS")}
        print(label, sym, {sp: (v.get("n"), v.get("gross_R"), v.get("net"), v.get("ci90")) for sp, v in res["oos"][label][sym].items()})
# diagnostics on the baseline: by session hour, side (CE/PE) and regime, OOS only
res["diag_oos"] = {}
for sym in rows_all:
    d = {}
    for nm, pred in (("CE", lambda x: x["side"] > 0), ("PE", lambda x: x["side"] < 0), ("trend", lambda x: ok(x, "A_trend")), ("range", lambda x: ok(x, "A_range")), ("hi_vol", lambda x: ok(x, "V_hi")), ("lo_vol", lambda x: ok(x, "V_lo")),
                     ("09:15-10:30", lambda x: x["_f"].minute <= 630), ("10:30-13:00", lambda x: 630 < x["_f"].minute <= 780), ("13:00-15:30", lambda x: x["_f"].minute > 780)):
        s = summ(split(rows_all[sym], sym, "OOS", pred), sym); d[nm] = {"n": s.get("n"), "gross_R": s.get("gross_R"), "net": s.get("net"), "ci90": s.get("ci90")}
    res["diag_oos"][sym] = d; print("DIAG", sym, {k: (v["n"], v["gross_R"], v["net"]) for k, v in d.items()})
# yearly walk-forward of the baseline and of the combined rule (no refit: the rules are fixed)
res["yearly"] = {}
for label, pred in (("BASELINE", lambda x: True), ("COMBINED", comb)):
    res["yearly"][label] = {}
    for sym in rows_all:
        ys = {}
        for y in range(2017, 2027):
            sel = [x for x in rows_all[sym] if x["day"] // 10000 == y and pred(x)]
            if len(sel) >= 15: s = summ(sel, sym); ys[y] = {"n": s["n"], "gross_R": s["gross_R"], "net": s["net"]}
        res["yearly"][label][sym] = ys
json.dump(res, open(os.path.join(HERE, "ablation.json"), "w"), indent=1, default=str)
