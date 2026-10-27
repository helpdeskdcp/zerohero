"""Offline analysis of a PineTS run of nifty_expiry_intelligence.pine (run_full_2y.json from pine_nifty_harness.py).
Everything here is recomputed from the run's plots + closed trades + the candles - it does not trust the script's own panel.
 1 per-entry statistics (partial exits summed), by direction / gap class / expiry session (calendar rule) / year-half, IS vs OOS, cost sensitivity, R-multiples
 2 hard-rule invariants (entry timing, trade caps, cooldown, flat by square-off, one position at a time, thresholds at the signal bar)
 3 independent recomputation of the virtual-signal outcomes and comparison with the script's running tracker
 4 chronological calibration study (train 60 % / test 40 %): is the evidence index informative about 'T1 before stop'? Brier vs base rate, reliability table
usage: pine_nifty_report.py run_full_2y.json [--out report.json]"""
import argparse, json, math, os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
IST = timezone(timedelta(hours=5, minutes=30))
nan = float("nan")


def load(path):
    sys.path.insert(0, os.path.join(HERE, "scripts")); import pine_nifty_harness as H
    d = json.load(open(path)); times = d["times"]; pl = H.unpack({p["title"]: [nan if v is None else float(v) for v in p["values"]] for p in d["plots"]})
    return d, times, pl


def expiry_days(first, last):
    """NIFTY weekly expiry sessions by rule: Thursday until 2025-08-31, Tuesday from 2025-09-01; a holiday moves it to the previous trading day. (Rule-based approximation.)"""
    from app import market_calendar as mc
    out, d = set(), first
    while d <= last:
        wd = 3 if d < datetime(2025, 9, 1).date() else 1
        if d.weekday() == wd:
            e = d
            while e.weekday() >= 5 or mc.is_holiday(e, "NSE"): e -= timedelta(days=1)
            out.add(e)
        d += timedelta(days=1)
    return out


def per_entry(trades):
    g = defaultdict(lambda: {"pnl": 0.0, "comm": 0.0, "gross_pts_units": 0.0, "units": 0.0, "dir": 0, "entry_price": None})
    for t in trades:
        k = t["entry_time"]; e = g[k]; e["pnl"] += t["profit"]; e["comm"] += t.get("commission", 0.0)
        d = 1 if t["size"] > 0 else -1; e["dir"] = d; e["units"] += abs(t["size"]); e["entry_price"] = t["entry_price"]
        e["gross_pts_units"] += (t["exit_price"] - t["entry_price"]) * d * abs(t["size"]); e["exit_time"] = max(t["exit_time"], e.get("exit_time", 0)); e["entry_bar"] = t["entry_bar"]
    return dict(sorted(g.items()))


def agg(entries):
    n = len(entries)
    if not n: return {"n": 0}
    pn = [e["pnl"] for e in entries]; w = [x for x in pn if x > 0]; l = [-x for x in pn if x <= 0]
    eq, peak, dd = 0.0, 0.0, 0.0
    for x in pn: eq += x; peak = max(peak, eq); dd = max(dd, peak - eq)
    return {"n": n, "win_rate": round(len(w) / n, 3), "net": round(sum(pn), 1), "profit_factor": round(sum(w) / sum(l), 3) if l and sum(l) > 0 else None,
            "expectancy": round(sum(pn) / n, 2), "avg_win": round(sum(w) / len(w), 1) if w else None, "avg_loss": round(-sum(l) / len(l), 1) if l else None, "max_dd": round(dd, 1)}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run"); ap.add_argument("--out"); a = ap.parse_args()
    d, times, pl = load(a.run); tr = d["strategy"]["closed_trades"]; n = len(times)
    sess = [datetime.fromtimestamp(t / 1000, IST) for t in times]
    ent = per_entry(tr); rep = {"bars": n, "first": sess[0].isoformat(), "last": sess[-1].isoformat(), "closed_trade_rows": len(tr), "entries": len(ent), "engine": "PineTS 0.13.0 (NOT TradingView)", "pine_summary": d["strategy"]["pine_summary"]}
    idx_of = {t: i for i, t in enumerate(times)}
    exp = expiry_days(sess[0].date(), sess[-1].date())
    rows = []
    for k, e in ent.items():
        i = idx_of[k]; si = i - 1                                       # signal bar = the bar before the fill bar
        gap = pl["Gap class code"][i]; sl = pl["Planned SL (PE)"][si] if e["dir"] < 0 else pl["Planned SL (CE)"][si]
        risk = abs(e["entry_price"] - sl) if not math.isnan(sl) else nan
        rows.append({**e, "i": i, "si": si, "date": sess[i].date(), "gap": gap, "risk": risk, "expiry": sess[i].date() in exp, "R": (e["gross_pts_units"] / e["units"] / risk) if risk and risk > 0 else nan, "net_idx": pl["Evidence index (net)"][si], "n_active": pl["Active components"][si]})
    rep["overall"] = agg(rows)
    rep["by_direction"] = {"CE (long)": agg([r for r in rows if r["dir"] > 0]), "PE (short)": agg([r for r in rows if r["dir"] < 0])}
    gcls = {-2: "exceptional down", -1: "gap down", 0: "flat", 1: "gap up", 2: "exceptional up"}
    rep["by_gap_class"] = {gcls.get(int(g), "unknown"): agg([r for r in rows if r["gap"] == g]) for g in sorted({r["gap"] for r in rows if not math.isnan(r["gap"])})}
    rep["by_expiry_session"] = {"expiry sessions (rule-based)": agg([r for r in rows if r["expiry"]]), "other sessions": agg([r for r in rows if not r["expiry"]])}
    k = int(len(rows) * 0.6); rep["in_sample_first60pct_of_entries"] = agg(rows[:k]); rep["out_of_sample_last40pct_of_entries"] = agg(rows[k:])
    half = {}
    for r in rows: half.setdefault(f"{r['date'].year}-H{1 if r['date'].month <= 6 else 2}", []).append(r)
    rep["by_half_year"] = {h: agg(v) for h, v in sorted(half.items())}
    Rs = [r["R"] for r in rows if not math.isnan(r["R"])]
    if Rs: rep["R_multiples_gross"] = {"n": len(Rs), "mean_R": round(sum(Rs) / len(Rs), 3), "median_R": round(sorted(Rs)[len(Rs) // 2], 3), "share_R_ge_1": round(sum(1 for x in Rs if x >= 1) / len(Rs), 3)}
    # cost sensitivity: gross (pre-cost) points x units vs notional-based bps costs
    gross = sum(r["gross_pts_units"] for r in rows); notional = sum(r["units"] * r["entry_price"] for r in rows)
    rep["cost_sensitivity"] = {"gross_pnl_before_any_cost": round(gross, 1), "entries": len(rows), "gross_pnl_per_entry": round(gross / max(len(rows), 1), 2),
                               "net_at_bps_per_side": {str(b): round(gross - 2 * b / 1e4 * notional, 1) for b in (0, 0.5, 1.0, 1.5, 2.0, 3.0)},
                               "declared_run_net": round(sum(r["pnl"] for r in rows), 1), "note": "declared run = 1.5 bps/side commission + 10 ticks (0.5 pt) slippage per fill"}
    # ---------------- invariants ----------------
    inv, viol = {}, defaultdict(list)
    day_entries = defaultdict(list)
    for r in rows: day_entries[r["date"]].append(r)
    for r in rows:
        mod = sess[r["i"]].hour * 60 + sess[r["i"]].minute; smod = sess[r["si"]].hour * 60 + sess[r["si"]].minute
        if pl["Signal (+1 CE / -1 PE)"][r["si"]] != r["dir"]: viol["entry without a signal on the previous bar"].append(str(sess[r["i"]]))
        if pl["Bar of day"][r["si"]] < 1: viol["signal on the first candle"].append(str(sess[r["i"]]))
        if smod >= 14 * 60 + 45: viol["signal at/after 14:45"].append(str(sess[r["i"]]))
        if abs(r["net_idx"]) < 0.35 - 1e-9 or r["n_active"] < 4: viol["threshold not met at the signal bar"].append(str(sess[r["i"]]))
        if pl["Gap class code"][r["si"]] in (2, -2): viol["entry on an exceptional-gap session"].append(str(sess[r["i"]]))
        ex = datetime.fromtimestamp(r["exit_time"] / 1000, IST)
        if ex.date() != r["date"] or ex.hour * 60 + ex.minute > 15 * 60 + 20: viol["position held past ~15:20 / overnight"].append(str(sess[r["i"]]))
    for dte, v in day_entries.items():
        if len(v) > 2: viol["more than max trades per session"].append(str(dte))
        v.sort(key=lambda x: x["i"])
        for x, y in zip(v, v[1:]):
            if y["i"] <= idx_of.get(x["exit_time"], 10 ** 9) - 0: viol["overlapping positions"].append(str(dte))
            if x["pnl"] < 0 and y["i"] - idx_of.get(x["exit_time"], 0) < 6: viol["entry inside the 6-bar cooldown after a loss"].append(str(dte))
    rep["invariants"] = {"checked_entries": len(rows), "violations": {k: len(v) for k, v in viol.items()}, "examples": {k: v[:3] for k, v in viol.items()}}
    # ---------------- virtual-signal outcomes (independent of the script's tracker) ----------------
    # ---------------- virtual signals (Pine tracker semantics re-implemented independently) ----------------
    sys.path.insert(0, os.path.join(HERE, "scripts")); import pine_nifty_harness as H
    cand = H.candles("5"); assert len(cand) == n and cand[0]["time"] == times[0] and cand[-1]["time"] == times[-1], "candle window differs from the run"
    O = [c["open"] for c in cand]; Hh = [c["high"] for c in cand]; Lw = [c["low"] for c in cand]
    HOR, RR1 = 24, 1.0
    sig = [(i, int(pl["Signal (+1 CE / -1 PE)"][i])) for i in range(n) if pl["Signal (+1 CE / -1 PE)"][i] in (1.0, -1.0)]
    vs, cnt = [], {1: [0, 0, 0], -1: [0, 0, 0]}                       # [resolved, wins, timeouts]
    for s0, dr in sig:
        stop = pl["Planned SL (CE)"][s0] if dr > 0 else pl["Planned SL (PE)"][s0]
        if math.isnan(stop) or s0 + 1 >= n: continue
        e0 = O[s0 + 1]; r0 = abs(e0 - stop)
        if (dr > 0 and e0 <= stop) or (dr < 0 and e0 >= stop): continue                    # 'badE': removed without counting
        t1 = e0 + dr * RR1 * r0; res = None
        for j in range(s0 + 1, n):
            hs = (dr > 0 and Lw[j] <= stop) or (dr < 0 and Hh[j] >= stop); ht = (dr > 0 and Hh[j] >= t1) or (dr < 0 and Lw[j] <= t1)
            if hs: res = "L"
            elif ht: res = "W"
            elif j - s0 >= HOR: res = "T"
            if res: break
        if not res: continue
        c = cnt[dr]
        if res == "T": c[2] += 1
        else:
            c[0] += 1; c[1] += 1 if res == "W" else 0
            vs.append({"i": s0, "dir": dr, "win": 1 if res == "W" else 0, "net_idx": pl["Evidence index (net)"][s0], "n_active": pl["Active components"][s0], "risk": r0})
    last = lambda k: pl[k][-1]
    rep["tracker_check"] = {"python_bull_resolved_wins_timeouts": cnt[1], "pine_bull": [last("Tracker bull resolved"), last("Tracker bull wins"), last("Tracker bull timeouts")],
                            "python_bear_resolved_wins_timeouts": cnt[-1], "pine_bear": [last("Tracker bear resolved"), last("Tracker bear wins"), last("Tracker bear timeouts")], "signals": len(sig)}
    rep["tracker_check"]["match"] = (cnt[1] == [int(last("Tracker bull resolved")), int(last("Tracker bull wins")), int(last("Tracker bull timeouts"))] and cnt[-1] == [int(last("Tracker bear resolved")), int(last("Tracker bear wins")), int(last("Tracker bear timeouts"))])
    # ---------------- chronological calibration study ----------------
    cal = {}
    for name, dr in (("bull (CE)", 1), ("bear (PE)", -1), ("both sides pooled", 0)):
        v = [x for x in vs if dr == 0 or x["dir"] == dr]; m = len(v)
        if m < 40: cal[name] = {"n": m, "status": "INSUFFICIENT SAMPLE (need >= 40 resolved signals for a 60/40 split)", "base_rate": round(sum(x["win"] for x in v) / m, 3) if m else None}; continue
        k = int(m * 0.6); tr_, te_ = v[:k], v[k:]; base = sum(x["win"] for x in tr_) / len(tr_)
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        X = lambda L: np.array([[abs(x["net_idx"]), x["n_active"]] + ([x["dir"]] if dr == 0 else []) for x in L]); y = lambda L: np.array([x["win"] for x in L])
        try:
            clf = LogisticRegression(C=1.0).fit(X(tr_), y(tr_)); pr = clf.predict_proba(X(te_))[:, 1]
            brier_m = float(np.mean((pr - y(te_)) ** 2)); brier_b = float(np.mean((base - y(te_)) ** 2))
            bins = [(0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 1.01)]; rel = []
            for lo, hi in bins:
                idx = [i for i, q in enumerate(pr) if lo <= q < hi]
                if idx: rel.append({"pred_range": [lo, min(hi, 1.0)], "n": len(idx), "mean_pred": round(float(np.mean(pr[idx])), 3), "observed": round(float(np.mean(y(te_)[idx])), 3)})
            auc = None
            if len(set(y(te_))) == 2:
                from sklearn.metrics import roc_auc_score
                auc = round(float(roc_auc_score(y(te_), pr)), 3)
            cal[name] = {"n": m, "train": len(tr_), "test": len(te_), "base_rate_train": round(base, 3), "test_win_rate": round(float(np.mean(y(te_))), 3), "brier_model": round(brier_m, 4), "brier_base_rate": round(brier_b, 4),
                         "model_beats_base_rate": brier_m < brier_b, "auc_test": auc, "reliability_test": rel, "verdict": "NOT CALIBRATED / no skill over the base rate" if brier_m >= brier_b else "model has lower Brier than the base rate on this one split (still small-sample; not a validated probability)"}
        except Exception as ex:
            cal[name] = {"n": m, "error": str(ex)[:120]}
    rep["calibration_study"] = cal
    json.dump(rep, open(a.out, "w"), indent=1, default=str) if a.out else None
    print(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__": main()
