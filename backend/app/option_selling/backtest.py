"""Short iron condor on real 1-minute NIFTY/SENSEX weekly option closes. Rules are fixed by the pre-registered spec."""
import json, os, sqlite3
from datetime import datetime, timedelta, timezone
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IST = timezone(timedelta(hours=5, minutes=30))
IDX = {"NIFTY": dict(db="option_flow.db", step=50, lot=65, slip=0.5), "SENSEX": dict(db="option_flow_sensex.db", step=100, lot=20, slip=2.0)}
VARIANTS = {"V0": (-1, "09:30", -1, "15:15"), "V1": (-2, "09:30", -2, "15:15"), "V2": (-2, "15:00", -1, "15:15")}   # (entry session idx, time, exit session idx, time)
BROKERAGE, EXCH, STAMP, SEBI, GST = 20.0, 0.0355299 / 100, 0.003 / 100, 0.0001 / 100, 0.18


def stt_rate(day): return 0.0015 if day >= "2026-04-01" else 0.0010


def cost(day, sells_in, buys_in, sells_out, buys_out, lot):
    """Rupee cost of one condor round trip. Each argument = list of leg prices (after slippage) for that side/phase."""
    sell_v = (sum(sells_in) + sum(sells_out)) * lot; buy_v = (sum(buys_in) + sum(buys_out)) * lot; tot = sell_v + buy_v
    brk = BROKERAGE * (len(sells_in) + len(buys_in) + len(sells_out) + len(buys_out))
    exch = EXCH * tot; sebi = SEBI * tot
    return brk + exch + sebi + stt_rate(day) * sell_v + STAMP * buy_v + GST * (brk + exch + sebi)


def epoch(day, hhmm): return int(datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=IST).timestamp())


class Chain:
    def __init__(self, c, expiry):
        self.s = {}
        for k, o, t, p in c.execute("SELECT strike, otype, ts, c FROM bars WHERE expiry=? AND c IS NOT NULL ORDER BY ts", (expiry,)):
            self.s.setdefault((k, o), {})[t] = p
        self.strikes = sorted({k for k, _ in self.s})

    def px(self, k, o, t, back=300):
        d = self.s.get((k, o))
        if not d: return None
        for x in range(t - 60, t - 60 - back - 1, -60):
            if x in d: return d[x]
        return None

    def last_known(self, k, o, t):
        d = self.s.get((k, o)); 
        if not d: return None
        for x in range(t - 60, t - 60 - 3600, -60):
            if x in d: return d[x]
        return None


def build(ch, t, step, slip, m):
    """Return dict(legs=(sc_k, sp_k, lc_k, lp_k), credit_px (mid), ...) or None."""
    best = None
    for k in ch.strikes:
        c, p = ch.px(k, "CE", t), ch.px(k, "PE", t)
        if c is None or p is None: continue
        if best is None or abs(c - p) < best[0]: best = (abs(c - p), k, c + p)
    if best is None: return None
    _, atm, st = best
    d = max(2 * step, round(m * st / step) * step); w = 2 * step
    legs = (atm + d, atm - d, atm + d + w, atm - d - w)
    pr = [ch.px(legs[0], "CE", t), ch.px(legs[1], "PE", t), ch.px(legs[2], "CE", t), ch.px(legs[3], "PE", t)]
    if any(x is None for x in pr): return None
    return dict(legs=legs, mid=pr, atm=atm, st=st, w=w)


def condor_value(ch, legs, t):
    """Mid cost to close the condor at t (None if a short leg has no recent price)."""
    p = [ch.last_known(legs[0], "CE", t), ch.last_known(legs[1], "PE", t), ch.last_known(legs[2], "CE", t), ch.last_known(legs[3], "PE", t)]
    if any(x is None for x in p): return None
    return p[0] + p[1] - p[2] - p[3]


def trade(ch, days, var, idx, m=1.0, target=None, stop=None):
    cfg = IDX[idx]; ie, it, xe, xt = VARIANTS[var]
    if len(days) < 2: return None, "FEW_DAYS"
    try: d_in, d_out = days[ie], days[xe]
    except IndexError: return None, "FEW_DAYS"
    t_in, t_out = epoch(d_in, it), epoch(d_out, xt)
    b = build(ch, t_in, cfg["step"], cfg["slip"], m)
    if b is None: return None, "NO_PRICE_IN"
    s = cfg["slip"]; sc, sp, lc, lp = b["mid"]
    sells_in = [max(0.05, sc - s), max(0.05, sp - s)]; buys_in = [lc + s, lp + s]
    credit = sum(sells_in) - sum(buys_in)
    if credit <= 0: return None, "NO_CREDIT"
    exit_t = t_out
    if target is not None or stop is not None:
        for t in range(t_in + 60, t_out, 60):
            v = condor_value(ch, b["legs"], t)
            if v is None: continue
            if target is not None and v <= (1 - target) * (sc + sp - lc - lp): exit_t = t; break
            if stop is not None and v - (sc + sp - lc - lp) >= stop * (sc + sp - lc - lp): exit_t = t; break
    xs = [ch.last_known(b["legs"][0], "CE", exit_t), ch.last_known(b["legs"][1], "PE", exit_t), ch.last_known(b["legs"][2], "CE", exit_t), ch.last_known(b["legs"][3], "PE", exit_t)]
    if any(x is None for x in xs): return None, "NO_PRICE_OUT"
    buys_out = [xs[0] + s, xs[1] + s]; sells_out = [max(0.0, xs[2] - s), max(0.0, xs[3] - s)]
    gross = (credit - (sum(buys_out) - sum(sells_out))) * cfg["lot"]
    c = cost(d_in, sells_in, buys_in, sells_out, buys_out, cfg["lot"])
    maxloss = (b["w"] - credit) * cfg["lot"]
    return dict(day=d_in, exp=d_out, gross=gross, cost=c, net=gross - c, credit=credit, maxloss=maxloss, legs=b["legs"], st=b["st"], exit_t=exit_t), None


def load(idx):
    cfg = IDX[idx]; c = sqlite3.connect(os.path.join(HERE, "data", "historical", "option_flow", cfg["db"]))
    out = []
    for e, days in c.execute("SELECT expiry, days FROM done ORDER BY expiry"):
        out.append((e, sorted(days.split(","))))
    return c, out


def stats(rows, skips, nboot=4000, level=0.05 / 6, seed=7):
    rows = sorted(rows, key=lambda r: r["day"]); n = len(rows)
    if n == 0: return dict(n=0, verdict="INSUFFICIENT_SAMPLE", skips=skips)
    x = np.array([r["net"] for r in rows]); months = np.array([r["day"][:7] for r in rows]); um = sorted(set(months))
    rng = np.random.default_rng(seed); groups = [x[months == mth] for mth in um]; bm = []
    for _ in range(nboot):
        pick = rng.integers(0, len(um), len(um)); bm.append(np.concatenate([groups[i] for i in pick]).mean())
    lo, hi = np.percentile(bm, [100 * level / 2, 100 * (1 - level / 2)])
    h = n // 2; h1, h2 = x[:h].mean(), x[h:].mean()
    srt = np.sort(x); k = max(1, int(np.ceil(0.05 * n))); es = srt[:k].mean()
    eq = np.cumsum(x); dd = float((eq - np.maximum.accumulate(eq)).min())
    wins = x[x > 0]; tail = bool(len(wins) and srt[0] < -10 * wins.mean())
    ok = n >= 80 and x.mean() > 0 and lo > 0 and h1 > 0 and h2 > 0
    return dict(n=n, mean=float(x.mean()), ci_lo=float(lo), ci_hi=float(hi), half1=float(h1), half2=float(h2), win=float((x > 0).mean()), worst=float(srt[0]), es5=float(es),
                maxdd=dd, total=float(x.sum()), gross_mean=float(np.mean([r["gross"] for r in rows])), cost_mean=float(np.mean([r["cost"] for r in rows])),
                max_possible_loss=float(max(r["maxloss"] for r in rows)), tail_flag=tail, skips=skips,
                verdict="GO-SHADOW" if ok else ("INSUFFICIENT_SAMPLE" if n < 80 else "NO-GO"))


def run_cell(idx, var, m=1.0, target=None, stop=None):
    c, exps = load(idx); rows = []; skips = {}
    for e, days in exps:
        ch = Chain(c, e); r, why = trade(ch, days, var, idx, m, target, stop)
        if r is None: skips[why] = skips.get(why, 0) + 1
        else: rows.append(r)
    c.close(); return stats(rows, skips), rows


if __name__ == "__main__":
    res = {}
    for idx in IDX:
        for var in VARIANTS:
            s, rows = run_cell(idx, var); res[f"{idx}_{var}"] = s; print(idx, var, json.dumps({k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items()}), flush=True)
    out = os.path.join(HERE, "data", "research", "option_selling"); json.dump(res, open(os.path.join(out, "results_primary.json"), "w"), indent=1)
