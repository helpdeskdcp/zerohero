"""EXPLORATORY v2 (post-hoc, after discovering that ai_paper_trades.pnl mixes units: premium POINTS when quantity=1, RUPEES otherwise). Unit-free:
return per trade = (exit-entry)/entry of the option premium, minus MODELLED round-trip costs (strategy_lab/institutional_edge cost models, lot sizes from instrument_profiles).
Index options: estimate_index_option_cost (FORMULA_ESTIMATE, slippage 0.75 pt/leg). MCX NG/CRUDE: validated flat per-lot profiles (+0.5 pt slippage). Others skipped. READ-ONLY."""
import json, math, os, sqlite3, sys
import numpy as np
from scipy import stats
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.institutional_edge.costs import estimate_cost, estimate_index_option_cost
from app.instrument_profiles import get_instrument_profile

db = sqlite3.connect("file:data/chanakya.db?mode=ro", uri=True, timeout=30); db.row_factory = sqlite3.Row
rows = [dict(r) for r in db.execute("SELECT trade_id,closed_ts,market,underlying,strategy,entry,exit_price,pnl,quantity FROM ai_paper_trades WHERE status='CLOSED' AND entry>0 AND exit_price IS NOT NULL")]
IDX = {"NIFTY", "BANKNIFTY", "SENSEX"}; MCX = {"NATURALGAS": "NATURALGAS_OPTION", "CRUDEOIL": "CRUDEOIL_OPTION"}
out, skipped = [], {}
for r in rows:
    u = r["underlying"]; e, x = r["entry"], r["exit_price"]
    try: lot = get_instrument_profile(u).lot_size.value
    except Exception: lot = None
    if u in IDX and lot:
        c = estimate_index_option_cost(e, x, lot, lots=1, slippage_points=0.75)["total_cost_points"]
    elif u in MCX and lot:
        est = estimate_cost("MCX", MCX[u], slippage_points=0.5); c = est.total_cost_points
        if c is None: skipped["no_cost_profile"] = skipped.get("no_cost_profile", 0) + 1; continue
    else:
        skipped[u] = skipped.get(u, 0) + 1; continue
    g = (x - e) / e * 100.0; n = (x - e - c) / e * 100.0
    out.append({**r, "gross_pct": g, "net_pct": n, "cost_pct": c / e * 100.0})


def wilson(k, n, z=1.96):
    p = k / n; d = 1 + z * z / n; cc = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)); return ((cc - h) / d, (cc + h) / d)


def an(rs, label):
    n = len(rs)
    if n < 10: return {"label": label, "n": n}
    net = np.array([r["net_pct"] for r in rs]); gr = np.array([r["gross_pct"] for r in rs]); k = int((net > 0).sum()); w = net[net > 0]; l = -net[net <= 0]
    be = float(l.mean() / (w.mean() + l.mean())) if len(w) and len(l) else None
    by = {}
    for r in rs: by.setdefault(str(r["closed_ts"])[:10], []).append(r["net_pct"])
    dm = np.array([np.mean(v) for v in by.values()]); sd = dm.std(ddof=1) if len(dm) > 2 else 0
    t = dm.mean() / (sd / math.sqrt(len(dm))) if sd > 0 else 0.0; p = float(1 - stats.t.cdf(t, len(dm) - 1)) if len(dm) > 2 else None
    lo, hi = wilson(k, n)
    return {"label": label, "n": n, "days": len(dm), "win_rate": k / n, "wilson95": [lo, hi], "breakeven_win_rate": be, "mean_gross_pct": float(gr.mean()), "mean_cost_pct": float(np.mean([r["cost_pct"] for r in rs])),
            "mean_net_pct": float(net.mean()), "median_net_pct": float(np.median(net)), "p_day_clustered_one_sided": p, "profit_factor": float(w.sum() / l.sum()) if l.sum() else None}


groups = [an(out, "ALL (modelled-cost net, unit-free)")]
for s in ("AUTOSCALP", "MANUAL"): groups.append(an([r for r in out if r["strategy"] == s], f"strategy={s}"))
for fam, us in (("INDEX_OPTIONS", IDX), ("MCX_NG_CRUDE", set(MCX))): groups.append(an([r for r in out if r["underlying"] in us], f"family={fam}"))
groups.append(an([r for r in out if r["strategy"] == "AUTOSCALP" and r["underlying"] in IDX], "AUTOSCALP x INDEX_OPTIONS"))
groups.append(an([r for r in out if r["strategy"] == "AUTOSCALP" and r["underlying"] in MCX], "AUTOSCALP x MCX_NG_CRUDE"))
os.makedirs("data/research/claim_audit", exist_ok=True); json.dump({"groups": groups, "skipped": skipped}, open("data/research/claim_audit/claim_audit_v2_2026-10-20.json", "w"), indent=1, default=str)
f = lambda x, d=2: "n/a" if x is None else f"{x:.{d}f}"
L = ["", "---", "## CORRECTION + exploratory v2 (post-hoc; supersedes the rupee sums in the previous addendum)",
     "**Correction:** `ai_paper_trades.pnl` mixes units (premium POINTS when quantity=1, RUPEES otherwise), so the earlier addendum's `sum` / `mean P&L` columns were apples-to-oranges. Win rates and the AUTOSCALP-vs-MANUAL ordering do not depend on the unit, but the rupee magnitudes (e.g. MANUAL -26,383) must not be quoted.",
     "Unit-free re-analysis: return = (exit-entry)/entry of the option premium, minus MODELLED costs (index options: formula estimate, 0.75 pt/leg slippage; MCX NG/CRUDE: validated per-lot profile + 0.5 pt). Instruments without a cost model are skipped: " + str(skipped), "",
     "| subset | n | days | win rate (Wilson95) | break-even WR | mean gross % | mean cost % | mean NET % | day-clustered p (mean>0) | PF |", "|---|---|---|---|---|---|---|---|---|---|"]
for g in groups:
    if g["n"] < 10: L.append(f"| {g['label']} | {g['n']} | too few | | | | | | | |"); continue
    L.append(f"| {g['label']} | {g['n']} | {g['days']} | {g['win_rate']:.1%} ({g['wilson95'][0]:.1%}-{g['wilson95'][1]:.1%}) | {f(g['breakeven_win_rate'], 3)} | {f(g['mean_gross_pct'])} | {f(g['mean_cost_pct'])} | {f(g['mean_net_pct'])} | {f(g['p_day_clustered_one_sided'], 3)} | {f(g['profit_factor'])} |")
L.append("\nStill exploratory: ledger rows are the same data the rules were tuned on; modelled (not contract-note) costs; no independent holdout. It cannot support a 'proven' claim.")
open("CLAIM_TEST_RESULT_2026-10-20.md", "a").write("\n".join(L)); print("\n".join(L))
