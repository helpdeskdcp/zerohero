"""Claim test C1 on the paper ledger, exactly as pre-registered in CLAIM_TEST_SPEC_2026-10-20.md (commit 3b5ab6a). READ-ONLY (sqlite mode=ro)."""
import json, math, os, sqlite3, sys
import numpy as np
from scipy import stats

db = sqlite3.connect("file:data/chanakya.db?mode=ro", uri=True, timeout=30); db.row_factory = sqlite3.Row
rows = [dict(r) for r in db.execute("SELECT trade_id,opened_ts,closed_ts,market,underlying,strategy,probability,net_pnl,gross_pnl,trading_cost,slippage_cost,cost_model_status FROM ai_paper_trades "
                                    "WHERE status='CLOSED' AND net_pnl IS NOT NULL")]


def wilson(k, n, z=1.96):
    if n == 0: return (float("nan"), float("nan"))
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def analyse(rs, label):
    n = len(rs)
    if n == 0: return {"label": label, "n": 0}
    net = np.array([r["net_pnl"] for r in rs], float); days = [str(r["closed_ts"])[:10] for r in rs]
    wins = net > 0; k = int(wins.sum()); w = net[wins]; l = -net[net <= 0]
    wr = k / n; lo, hi = wilson(k, n)
    be = float(l.mean() / (w.mean() + l.mean())) if len(w) and len(l) else float("nan")
    by = {}
    for d, v in zip(days, net): by.setdefault(d, []).append(v)
    dm = np.array([np.mean(v) for v in by.values()]); nd = len(dm)
    m = float(net.mean()); se = float(net.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    p_day = None
    if nd >= 3:
        t = dm.mean() / (dm.std(ddof=1) / math.sqrt(nd)) if dm.std(ddof=1) > 0 else 0.0
        p_day = float(1 - stats.t.cdf(t, nd - 1))
    eq = np.cumsum(net); dd = float(np.max(np.maximum.accumulate(eq) - eq))
    out = {"label": label, "n": n, "days": nd, "win_rate": wr, "wilson95": [lo, hi], "breakeven_win_rate": be, "mean_net": m, "se": se, "t": (m / se if se else None),
           "p_day_clustered_one_sided": p_day, "sum_net": float(net.sum()), "profit_factor": float(w.sum() / l.sum()) if l.sum() > 0 else None, "max_drawdown": dd,
           "avg_win": float(w.mean()) if len(w) else None, "avg_loss": float(l.mean()) if len(l) else None, "flat": int((net == 0).sum()), "gross_sum": float(sum((r["gross_pnl"] or 0) for r in rs)),
           "cost_sum": float(sum((r["trading_cost"] or 0) + (r["slippage_cost"] or 0) for r in rs))}
    pr = [(r["probability"], 1.0 if r["net_pnl"] > 0 else 0.0) for r in rs if r["probability"] is not None]
    if len(pr) >= 10:
        p = np.array([x[0] for x in pr], float); p = p / 100.0 if p.max() > 1.5 else p; y = np.array([x[1] for x in pr])
        bins = np.minimum((p * 10).astype(int), 9); ece = sum(abs(p[bins == b].mean() - y[bins == b].mean()) * (bins == b).mean() for b in range(10) if (bins == b).any())
        out["calibration"] = {"n": len(pr), "mean_pred": float(p.mean()), "realised": float(y.mean()), "brier": float(np.mean((p - y) ** 2)), "ece10": float(ece)}
    return out


P = analyse(rows, "PRIMARY: all closed trades")
verdict = ("INCONCLUSIVE" if (P["n"] < 100 or P["days"] < 30) else
           "PASS" if (P["mean_net"] > 0 and (P["p_day_clustered_one_sided"] or 1) <= 0.05 and P["wilson95"][0] > P["breakeven_win_rate"]) else "FAIL")
cal = P.get("calibration"); cal_pass = bool(cal and abs(cal["mean_pred"] - cal["realised"]) <= 0.05 and cal["ece10"] <= 0.08)
fam = lambda r: "INDEX_OPTIONS(NIFTY/BANKNIFTY/SENSEX)" if r["underlying"] in ("NIFTY", "BANKNIFTY", "SENSEX") else ("MCX" if r["market"] == "MCX" else "OTHER_EQUITY")
expl = [analyse([r for r in rows if fam(r) == f], f"EXPLORATORY {f}") for f in sorted({fam(r) for r in rows})]
expl.append(analyse([r for r in rows if str(r["closed_ts"])[:10] >= "2026-09-21"], "EXPLORATORY closed on/after 2026-09-21 (post calibration fix)"))
res = {"spec_commit": "3b5ab6a", "primary": P, "C1_verdict": verdict, "calibration_claim_pass": cal_pass, "exploratory": expl, "period": [min(str(r["closed_ts"])[:10] for r in rows), max(str(r["closed_ts"])[:10] for r in rows)]}
os.makedirs("data/research/claim_audit", exist_ok=True)
json.dump(res, open("data/research/claim_audit/claim_audit_2026-10-20.json", "w"), indent=1, default=str)
f = lambda x, d=3: "n/a" if x is None else (f"{x:.{d}f}" if isinstance(x, float) else str(x))
L = ["# ZeroHero claim test C1 — result (spec: CLAIM_TEST_SPEC_2026-10-20.md @3b5ab6a)", "", f"period {res['period']} · **C1 verdict: {verdict}** · calibration claim: {'PASS' if cal_pass else 'FAIL/NA'}", ""]
for a in [P] + expl:
    L.append(f"## {a['label']}")
    if a["n"] == 0: L.append("no trades"); continue
    L.append(f"- n={a['n']} days={a['days']} · win rate {a['win_rate']:.1%} (Wilson95 {a['wilson95'][0]:.1%}..{a['wilson95'][1]:.1%}) · break-even win rate {f(a['breakeven_win_rate'])} · flat {a['flat']}")
    L.append(f"- mean net/trade {f(a['mean_net'], 2)} (SE {f(a['se'], 2)}, t {f(a['t'], 2)}) · day-clustered one-sided p {f(a['p_day_clustered_one_sided'])} · sum net {f(a['sum_net'], 0)} (gross {f(a['gross_sum'], 0)}, costs {f(a['cost_sum'], 0)}) · PF {f(a['profit_factor'], 2)} · max DD {f(a['max_drawdown'], 0)}")
    if "calibration" in a: L.append(f"- calibration: predicted {a['calibration']['mean_pred']:.3f} vs realised {a['calibration']['realised']:.3f} · Brier {a['calibration']['brier']:.3f} · ECE {a['calibration']['ece10']:.3f} (n={a['calibration']['n']})")
open("CLAIM_TEST_RESULT_2026-10-20.md", "w").write("\n".join(L)); print("\n".join(L))
