"""READ-ONLY, PAPER-ONLY causal replay of app.hybrid_orderflow.scoring on the real Dhan ticks (5m bars built from ticks).

Pre-registered: policy thresholds are used AS SHIPPED (no tuning). At each closed 5m bar only data up to that bar is used. Entry = NEXT bar
open, exits at +3/+6 bars close; a missing bar is never forward-filled (that trade is skipped). Costs 2 bps round trip.
This is a plumbing/behaviour check and a descriptive look at SIGNAL bars -- the sample is a handful of sessions, so it can NOT validate an edge.
Usage: python scripts/orderflow_scoring_replay.py"""
import collections
import datetime as dt
import random
import sqlite3
import sys

import numpy as np

sys.path.insert(0, ".")
from app.engines.orderflow_engine import FootprintBar  # noqa: E402
from app.hybrid_orderflow import scoring as S  # noqa: E402
from app.hybrid_orderflow.policy import load_policy  # noqa: E402
from app.liquidity_sweep.sweep import detect_sweep  # noqa: E402

P = load_policy()
SYMS = {570750: "NATURALGAS", 569900: "CRUDEOIL", 48704: "NIFTY", 48699: "BANKNIFTY"}      # SENSEX fut: too few ticks
BAR = 300
LEN = P["smc"]["pivot_len"]
MINB = P["data"]["min_session_bars_for_features"]
COST_BPS = 2.0
HORIZ = (3, 6)
IST = 19800

db = sqlite3.connect("file:data/dhan_orderflow_ticks.db?mode=ro", uri=True)
rows = db.execute("select security_id,ltt,ltp,ltq,side,classification_method,best_bid,best_ask from classified_ticks "
                  f"where ltt>1500000000 and security_id in ({','.join(map(str, SYMS))}) order by security_id,ltt,id").fetchall()
ticks = collections.defaultdict(list)
dropped = 0
for sid, ltt, ltp, ltq, side, meth, bb, ba in rows:
    if bb and ba and bb >= ba:
        dropped += 1
        continue
    ticks[sid].append((ltt, ltp, ltq, side, meth))


# exchange tick (rupees) = Dhan master SEM_TICK_SIZE / 100 (paise), cross-checked against the observed price grid on 2026-10-17
TICKS = {570750: 0.1, 569900: 1.0, 48704: 0.1, 48699: 0.2}


def infer_tick(ts, sid=None):
    return TICKS[sid]


def build_bars(sid):
    ts = ticks[sid]
    tick = infer_tick(ts, sid)
    bars = collections.OrderedDict()
    for ltt, ltp, ltq, side, meth in ts:
        k = (ltt + IST) // BAR
        b = bars.get(k)
        if b is None:
            b = bars[k] = {"fp": FootprintBar(k * BAR - IST, BAR, ltp, tick_size=tick), "vol": 0, "pv": 0.0, "h": ltp, "l": ltp, "o": ltp, "c": ltp}
        b["fp"].add({"ltp": ltp, "ltq": max(int(ltq), 0), "side": side, "classification_method": meth})
        b["vol"] += max(int(ltq), 0)
        b["pv"] += ltp * max(int(ltq), 0)
        b["h"], b["l"], b["c"] = max(b["h"], ltp), min(b["l"], ltp), ltp
    return tick, [(k, v) for k, v in bars.items()]


def replay(sid):
    sym = SYMS[sid]
    tick, bars = build_bars(sid)
    out = []
    bystart = {k: v for k, v in bars}
    day_prev = {}                                   # IST day -> (high, low)
    days = collections.OrderedDict()
    for k, v in bars:
        days.setdefault(k * BAR // 86400, []).append((k, v))
    prior_hl = None
    for day, dbars in days.items():
        sw_h = sw_l = None
        sdir = 0
        events = []
        cvd = []
        cum = 0
        pv = vol = 0.0
        ema = None
        hist = []
        for i, (k, v) in enumerate(dbars):
            hist.append(v)
            fp = v["fp"]
            cum += fp.buy_vol - fp.sell_vol
            cvd.append(cum)
            pv, vol = pv + v["pv"], vol + v["vol"]
            ema = v["c"] if ema is None else ema + (v["c"] - ema) * 2 / 21
            j = i - LEN                                       # pivot candidate, confirmed by LEN closed bars to its right
            if j - LEN >= 0:
                hj, lj = hist[j]["h"], hist[j]["l"]
                if all(hj > hist[x]["h"] for x in range(j - LEN, j)) and all(hj >= hist[x]["h"] for x in range(j + 1, j + LEN + 1)):
                    sw_h = hj
                if all(lj < hist[x]["l"] for x in range(j - LEN, j)) and all(lj <= hist[x]["l"] for x in range(j + 1, j + LEN + 1)):
                    sw_l = lj
            prev_c = hist[i - 1]["c"] if i else None
            if sw_h is not None and prev_c is not None:
                if v["c"] > sw_h and prev_c <= sw_h:
                    events.append({"type": "BOS" if sdir != -1 else "CHOCH", "direction": S.BULL, "bar_index": i, "close_confirmed": True}); sdir = 1
                elif v["h"] > sw_h and v["c"] <= sw_h:
                    events.append({"type": "BOS", "direction": S.BULL, "bar_index": i, "close_confirmed": False})
            if sw_l is not None and prev_c is not None:
                if v["c"] < sw_l and prev_c >= sw_l:
                    events.append({"type": "BOS" if sdir != 1 else "CHOCH", "direction": S.BEAR, "bar_index": i, "close_confirmed": True}); sdir = -1
                elif v["l"] < sw_l and v["c"] >= sw_l:
                    events.append({"type": "BOS", "direction": S.BEAR, "bar_index": i, "close_confirmed": False})
            if i + 1 < MINB:
                continue                                       # warm-up: policy data.min_session_bars_for_features
            # ---- evidence (causal: bars[:i+1] only)
            levels = []
            if prior_hl:
                levels += [{"price": prior_hl[0], "source": "PDH"}, {"price": prior_hl[1], "source": "PDL"}]
            if i >= 5:
                levels += [{"price": max(b["h"] for b in hist[:i - 3]), "source": "RANGE_HIGH"}, {"price": min(b["l"] for b in hist[:i - 3]), "source": "RANGE_LOW"}]
            win = [{"h": b["h"], "l": b["l"], "c": b["c"]} for b in hist[-8:]]
            sw = detect_sweep(win, levels, reclaim_window=P["smc"]["sweep"]["reaction_bars"])
            if sw:
                d = sw.to_dict(); d["reclaim_bar_index"] = i - (len(win) - 1 - d["reclaim_bar_index"]); sw = d
            tot = fp.buy_vol + fp.sell_vol
            cf = tot / v["vol"] if v["vol"] else None
            dpct = (fp.buy_vol - fp.sell_vol) / tot if tot else None
            slope = cvd[-1] - cvd[-4] if len(cvd) >= 4 else None
            vwap = pv / vol if vol else None
            trend = S.BULL if v["c"] > ema else S.BEAR
            vols = [b["vol"] for b in hist[-13:-1]]
            rvol = v["vol"] / (sum(vols) / len(vols)) if len(vols) == 12 and sum(vols) else None
            kk = k // 3 - 1                                    # last COMPLETED 15m bucket (3 x 5m)
            h15 = [b for kx, b in dbars if kx // 3 == kk]
            htf = None
            if h15:
                htf = S.BULL if h15[-1]["c"] > h15[0]["o"] else S.BEAR if h15[-1]["c"] < h15[0]["o"] else None
            ev = [S.structure_evidence(events, i, P), S.sweep_evidence(sw, i, P),
                  S.delta_cvd_evidence(dpct, slope, cf, S.INFERRED, P), S.imbalance_evidence(fp.stacked_imbalances(), P),
                  S.vwap_evidence(v["c"], vwap, trend, P), S.rvol_evidence(rvol, P), S.htf_evidence(htf, True, P)]
            hh = ((k * BAR + BAR) % 86400) // 60
            r = S.score(ev, {"symbol": sym, "now_hhmm_ist": f"{hh // 60:02d}:{hh % 60:02d}", "kill_switch": False, "data_stale": False,
                             "last_signal": last_sig.get(sym)}, P)
            if r["status"] == "SIGNAL":
                last_sig[sym] = {"direction": r["direction"], "age_sec": 0, "_k": k}
            elif sym in last_sig:
                last_sig[sym]["age_sec"] = (k - last_sig[sym]["_k"]) * BAR
            fw = {}
            for h in HORIZ:
                a, b = bystart.get(k + 1), bystart.get(k + h)
                if a and b and (k + 1) in bystart and (k + h) in bystart and (k + h) // (86400 // BAR) == k // (86400 // BAR):
                    fw[h] = (b["c"] / a["o"] - 1) * 1e4
            out.append({"sym": sym, "k": k, "status": r["status"], "dir": r["direction"], "total": r["total"], "reasons": r["reasons"], "fw": fw,
                        "comp": {c["component"]: c["points"] for c in r["components"]}, "creasons": {c["component"]: c["reasons"] for c in r["components"]}})
        prior_hl = (max(b["h"] for b in hist), min(b["l"] for b in hist))
    return tick, out


last_sig = {}
allr = []
print(f"ticks used={sum(len(v) for v in ticks.values())} crossed/locked dropped={dropped}")
for sid in SYMS:
    tick, out = replay(sid)
    allr += out
    nd = len({o['k'] * BAR // 86400 for o in out})
    print(f"{SYMS[sid]:11s} tick_size={tick} scored_bars={len(out)} days={nd}")

st = collections.Counter(o["status"] for o in allr)
print(f"\nSTATUS counts: {dict(st)}")
print("top WAIT/NO_TRADE reasons:", collections.Counter(r for o in allr if o["status"] != "SIGNAL" for r in o["reasons"]).most_common(6))
print("\ncomponent availability (mean points | share of bars with points>0 | most common reason when 0):")
for c in P["scoring"]["components_max"]:
    pts = [o["comp"].get(c, 0) for o in allr]
    rs = collections.Counter(r for o in allr if not o["comp"].get(c, 0) for r in o["creasons"].get(c, []))
    print(f"  {c:21s} mean={np.mean(pts):.2f}  active={np.mean([p > 0 for p in pts])*100:5.1f}%  {rs.most_common(1)}")

net = lambda x, d: (x if d == S.BULL else -x) - COST_BPS
gross = lambda x, d: x if d == S.BULL else -x
print("\nforward result in signal direction (bps, entry next-bar open, exit +H bars close; missing bars skipped):")
rng = random.Random(7)
pool = {h: [(o["fw"][h], o["dir"]) for o in allr if o["dir"] and h in o["fw"]] for h in HORIZ}      # every bar that had a lead direction (SIGNAL or WAIT)
for h in HORIZ:
    sig = [(o["fw"][h], o["dir"]) for o in allr if o["status"] == "SIGNAL" and h in o["fw"]]
    wl = [(o["fw"][h], o["dir"]) for o in allr if o["status"] == "WAIT" and o["dir"] and h in o["fw"]]
    if not sig:
        print(f"  H={h}: SIGNAL n=0")
        continue
    g = np.array([gross(x, d) for x, d in sig])
    null = [np.mean([gross(*rng.choice(pool[h])) for _ in range(len(sig))]) for _ in range(2000)]
    p = float(np.mean([m >= g.mean() for m in null]))
    wg = np.mean([gross(x, d) for x, d in wl]) if wl else float("nan")
    print(f"  H={h}: SIGNAL n={len(sig)} gross mean={g.mean():+.1f}bps net(2bps)={g.mean()-COST_BPS:+.1f} hit={np.mean(g>0)*100:.0f}% | "
          f"WAIT-with-lead n={len(wl)} gross mean={wg:+.1f} | random-lead-bar null p(mean>=signal)={p:.2f}")
print("\nSIGNAL bars (IST bar-open | symbol | direction | total | fwd bps H3/H6 in signal direction):")
for o in allr:
    if o["status"] == "SIGNAL":
        t = dt.datetime.fromtimestamp(o["k"] * BAR - IST, dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%m-%d %H:%M")
        f = lambda h: f"{gross(o['fw'][h], o['dir']):+.0f}" if h in o["fw"] else "n/a"
        print(f"  {t} {o['sym']:10s} {o['dir']:8s} {o['total']:.0f}  {f(3)}/{f(6)}")
print("\nREAD: tiny sample, overlapping horizons, thresholds untuned. Behaviour/plumbing check only; NOT a validation of any edge.")
