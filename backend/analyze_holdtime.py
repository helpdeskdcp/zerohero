#!/usr/bin/env python3
"""Hold-time evidence analysis for the autonomous scalper (evidence-first).

Reads ai_paper_trades + scalp_signals (LIVE), buckets closed AUTOSCALP trades by
holding duration, and reports per-bucket win rate / expectancy / net points /
premature-exit rate / missed-profit (MFE captured vs left on the table).

Then simulates alternative max_hold_sec caps against the SAME trades using each
trade's recorded MFE/MAE path proxy, and states whether the evidence justifies a
change (needs a minimum sample; never tune on 1-2 trades).

Usage:  ./venv/bin/python data/analyze_holdtime.py [SYMBOL] [--since YYYY-MM-DD]
"""
import os
import sys
import sqlite3
from collections import defaultdict

DB = os.environ.get("CHANAKYA_DB_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "chanakya.db")
MIN_SAMPLE = 20          # do not draw conclusions below this many closed trades
BUCKETS = [(0, 300), (300, 600), (600, 900), (900, 1500), (1500, 2400),
           (2400, 3600), (3600, 10 ** 9)]


def rows(sym, since):
    q = ("SELECT t.trade_id, t.underlying, t.opened_ts, t.closed_ts, t.max_hold_sec, "
         "  t.exit_reason, t.result, t.entry, t.exit_price, t.stop_loss, "
         "  ROUND(t.pnl,3) pnl, ROUND(t.mfe,3) mfe, ROUND(t.mae,3) mae, ROUND(t.risk_ref,3) risk_ref, "
         "  CAST((julianday(t.closed_ts)-julianday(t.opened_ts))*86400 AS INT) held_s, "
         "  s.direction, s.confidence, s.regime "
         "FROM ai_paper_trades t "
         "LEFT JOIN scalp_signals s ON s.signal_id = t.signal_id "
         "WHERE t.strategy='AUTOSCALP' AND t.status='CLOSED' ")
    p = []
    if sym:
        q += "AND t.underlying=? "; p.append(sym)
    if since:
        q += "AND t.opened_ts >= ? "; p.append(since)
    q += "ORDER BY t.opened_ts"
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(q, p).fetchall()]
    finally:
        c.close()


def pct(n, d):
    return round(100.0 * n / d, 1) if d else None


def summarize(trs, label):
    if not trs:
        print(f"  {label}: (no trades)"); return
    n = len(trs)
    w = sum(t["result"] == "WIN" for t in trs)
    loss = sum(t["result"] == "LOSS" for t in trs)
    net = round(sum((t["pnl"] or 0) for t in trs), 2)
    rs = [(t["pnl"] or 0) / t["risk_ref"] for t in trs if t.get("risk_ref")]
    exp_r = round(sum(rs) / len(rs), 3) if rs else None
    # premature exit = TIME/TIME_NODATA exit that had meaningful open profit (MFE
    # >= 0.5R) it failed to bank -> the hold clock cut a live winner
    time_exits = [t for t in trs if (t["exit_reason"] or "").startswith("TIME")]
    premature = [t for t in time_exits
                 if t.get("risk_ref") and (t["mfe"] or 0) >= 0.5 * t["risk_ref"]
                 and (t["pnl"] or 0) < (t["mfe"] or 0) * 0.6]
    missed = round(sum(((t["mfe"] or 0) - max(0.0, t["pnl"] or 0)) for t in premature), 2)
    print(f"  {label}: n={n}  W/L={w}/{loss}  win%={pct(w, n)}  net={net}pts  "
          f"exp={exp_r}R  TIMEexits={len(time_exits)}  premature={len(premature)}  "
          f"missed_profit≈{missed}pts")


def bucket_analysis(trs):
    print("\n== holding-time buckets ==")
    by = defaultdict(list)
    for t in trs:
        h = t["held_s"] or 0
        for lo, hi in BUCKETS:
            if lo <= h < hi:
                by[(lo, hi)].append(t); break
    for (lo, hi) in BUCKETS:
        b = by.get((lo, hi), [])
        lbl = f"{lo//60:>2}-{'∞' if hi > 10**8 else hi//60:>3}m"
        summarize(b, lbl)


def exit_reason_breakdown(trs):
    print("\n== exit reasons ==")
    d = defaultdict(lambda: [0, 0.0])
    for t in trs:
        k = t["exit_reason"] or "(none)"
        d[k][0] += 1; d[k][1] += (t["pnl"] or 0)
    for k, (n, pnl) in sorted(d.items(), key=lambda kv: -kv[1][0]):
        print(f"  {k:12s} n={n:<3} net={round(pnl, 2)}pts")


def dimension(trs, key):
    print(f"\n== by {key} ==")
    d = defaultdict(list)
    for t in trs:
        d[t.get(key) or "(none)"].append(t)
    for k, b in sorted(d.items()):
        summarize(b, str(k))


def simulate_cap(trs, cap):
    """Re-score each trade under an alternative max_hold_sec cap.
    Proxy model (documented, conservative):
      - if held_s <= cap: outcome unchanged (the cap didn't bind)
      - if held_s > cap and exit was TIME*: we'd still have TIME'd, at ~the
        same price -> pnl unchanged (can't know the mid-path price; do NOT
        assume we'd have captured MFE — that would bias toward widening)
      - if held_s > cap and exit was TARGET/TRAIL/STOP: that exit happened
        AFTER `cap`, so under the tighter cap we'd have TIME'd out earlier at
        an unknown price; model as pnl=0 (flat scratch) — conservative.
    This intentionally makes WIDER caps look neutral and TIGHTER caps look
    slightly worse unless the tighter cap never binds. It cannot manufacture
    evidence for widening.
    """
    n = len(trs); net = 0.0; w = 0
    for t in trs:
        h = t["held_s"] or 0
        if h <= cap:
            net += (t["pnl"] or 0); w += t["result"] == "WIN"
        elif (t["exit_reason"] or "").startswith("TIME"):
            net += (t["pnl"] or 0); w += t["result"] == "WIN"
        else:
            net += 0.0  # earlier forced scratch
    return {"cap": cap, "net_pts": round(net, 2), "win%": pct(w, n)}


def main():
    args = [a for a in sys.argv[1:]]
    since = None
    if "--since" in args:
        i = args.index("--since"); since = args[i + 1]; del args[i:i + 2]
    sym = args[0].upper() if args else "NIFTY"
    trs = rows(sym, since)
    print(f"# hold-time evidence — {sym}" + (f" since {since}" if since else "")
          + f"   ({len(trs)} closed AUTOSCALP trades)")
    if len(trs) < MIN_SAMPLE:
        print(f"\n*** SAMPLE TOO SMALL ({len(trs)} < {MIN_SAMPLE}) — "
              f"report descriptive stats only, DO NOT tune max_hold_sec. ***")
    summarize(trs, "ALL")
    exit_reason_breakdown(trs)
    bucket_analysis(trs)
    dimension(trs, "confidence")
    dimension(trs, "regime")
    dimension(trs, "direction")

    print("\n== max_hold_sec what-if (conservative proxy — cannot favour widening) ==")
    cur = trs[0]["max_hold_sec"] if trs else None
    print(f"  current effective cap on these trades: "
          f"{sorted({t['max_hold_sec'] for t in trs}) if trs else '-'}")
    for cap in (600, 900, 1200, 1500, 1800, 2400, 3000):
        s = simulate_cap(trs, cap)
        mark = "  <- current" if cap == cur else ""
        print(f"  cap {cap:>5}s: net {s['net_pts']:>8}pts  win% {s['win%']}{mark}")

    print("\n== verdict ==")
    if len(trs) < MIN_SAMPLE:
        print(f"  INSUFFICIENT EVIDENCE ({len(trs)}/{MIN_SAMPLE}). Keep max_hold_sec as-is. "
              f"Collect more sessions.")
    else:
        base = simulate_cap(trs, cur or 2400)["net_pts"]
        alts = {c: simulate_cap(trs, c)["net_pts"] for c in (900, 1500, 1800, 3000)}
        best = max(alts.items(), key=lambda kv: kv[1])
        improve = best[1] - base
        print(f"  current cap net={base}pts ; best alt cap {best[0]}s net={best[1]}pts "
              f"(Δ {round(improve, 2)}pts over {len(trs)} trades)")
        if improve > abs(base) * 0.15 and improve > 5:
            print(f"  -> evidence SUGGESTS testing cap {best[0]}s. Requires: "
                  f"before/after A/B over a further sample + regression test + rollback plan.")
        else:
            print("  -> NO material improvement from any alternative cap. "
                  "Keep max_hold_sec unchanged.")


if __name__ == "__main__":
    main()
