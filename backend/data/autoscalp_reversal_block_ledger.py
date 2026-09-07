#!/usr/bin/env python3
"""
Forward ledger for the AUTOSCALP "REVERSAL-setup block" experiment.

Change applied 2026-09-07T16:45:25Z: on the 4 non-frozen symbols
(NATURALGAS, CRUDEOIL, BANKNIFTY, SENSEX) the strategy config now blocks
signal_type in {SUPPORT_REVERSAL, RESISTANCE_REVERSAL} (RESISTANCE_BREAKOUT
was already blocked). NIFTY is untouched (frozen) and acts as a control.

Rollback: write data/autoscalp_config_pre_reversal_block_20260907T164525Z.json
back into app_settings.autoscalp_config.

This script is READ-ONLY. Re-run after each session:
    python3 data/autoscalp_reversal_block_ledger.py            # refresh + rewrite the .md
    python3 data/autoscalp_reversal_block_ledger.py --print    # also echo to stdout
"""
import sqlite3, statistics as st, sys, pathlib
from datetime import datetime, timezone, timedelta

DB = str(pathlib.Path(__file__).resolve().parents[1] / "data" / "chanakya.db")
OUT = pathlib.Path(__file__).resolve().parent / "autoscalp_reversal_block_ledger.md"
CUTOFF = "2026-09-07T16:45:25+00:00"          # change-applied timestamp (UTC)
TARGETS = {"NATURALGAS", "CRUDEOIL", "BANKNIFTY", "SENSEX"}
BLOCKED_SETUPS = {"SUPPORT_REVERSAL", "RESISTANCE_REVERSAL"}
IST = timezone(timedelta(hours=5, minutes=30))

# ---- FROZEN PRE-CHANGE BASELINE (computed once, 2026-08-31..09-07, 80 decided) ----
BASELINE = {
    "window": "2026-08-31 .. 2026-09-07 (6 sessions)",
    "decided": 80, "wins": 44, "losses": 36, "flat": 12,
    "win_rate": 55.0, "expectancy_pt": 0.05, "net_pt": 4.2, "profit_factor": 1.02,
    "avg_win": 4.11, "avg_loss": -4.91, "max_dd_pt": -66.3,
    "by_setup": {
        "SUPPORT_BREAKDOWN":   {"n": 42, "wr": 59.5, "exp": 2.25, "pf": 2.75, "net": 94.6},
        "SUPPORT_REVERSAL":    {"n": 25, "wr": 56.0, "exp": -1.72, "pf": 0.28, "net": -43.0},
        "RESISTANCE_REVERSAL": {"n": 13, "wr": 38.5, "exp": -3.65, "pf": 0.25, "net": -47.4},
    },
    # per-symbol decided-trade net, pre-change
    "by_symbol": {"NATURALGAS": -0.9, "CRUDEOIL": -22.3, "NIFTY": -8.6,
                  "BANKNIFTY": 36.1, "SENSEX": 0.0},
}


def _metrics(rows):
    d = [r for r in rows if r["result"] in ("WIN", "LOSS")]
    if not d:
        return dict(n=0, dec=0, wr=None, exp=None, pf=None, net=0.0,
                    avg_w=None, avg_l=None, max_dd=0.0, flat=len(rows) - len(d))
    w = [r for r in d if r["result"] == "WIN"]
    l = [r for r in d if r["result"] == "LOSS"]
    gw = sum(r["pnl"] or 0 for r in w)
    gl = -sum(r["pnl"] or 0 for r in l)
    cum = peak = dd = 0.0
    for r in sorted(d, key=lambda x: x["opened_ts"]):
        cum += r["pnl"] or 0
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return dict(
        n=len(rows), dec=len(d), flat=len(rows) - len(d),
        wr=round(100 * len(w) / len(d), 1),
        exp=round(sum(r["pnl"] or 0 for r in d) / len(d), 2),
        pf=(round(gw / gl, 2) if gl else float("inf")),
        net=round(sum(r["pnl"] or 0 for r in d), 1),
        avg_w=round(st.mean([r["pnl"] or 0 for r in w]), 2) if w else None,
        avg_l=round(st.mean([r["pnl"] or 0 for r in l]), 2) if l else None,
        max_dd=round(dd, 1),
    )


def _fmt(m):
    if not m["dec"]:
        return f"n={m['n']:>3} (0 decided, {m['flat']} flat/no-data)"
    pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    return (f"n={m['n']:>3} dec={m['dec']:>3} WR={m['wr']:>5.1f}%  exp={m['exp']:+6.2f}pt  "
            f"PF={pf:>5}  net={m['net']:+7.1f}  avgW={m['avg_w']}  avgL={m['avg_l']}  maxDD={m['max_dd']:+.1f}")


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    allrows = [dict(r) for r in c.execute(
        "SELECT * FROM ai_paper_trades WHERE strategy LIKE 'AUTOSCALP%' AND status='CLOSED' "
        "ORDER BY opened_ts")]
    fwd = [r for r in allrows if r["opened_ts"] > CUTOFF]
    now = datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M IST")

    L = []
    p = L.append
    p(f"# AUTOSCALP — REVERSAL-block forward ledger")
    p("")
    p(f"_regenerated: {now} · cutoff (change applied): {CUTOFF} · read-only_")
    p("")
    p("**Change:** block `SUPPORT_REVERSAL` + `RESISTANCE_REVERSAL` on "
      "NATURALGAS / CRUDEOIL / BANKNIFTY / SENSEX. NIFTY = untouched control.")
    p("**Rollback:** restore `data/autoscalp_config_pre_reversal_block_20260907T164525Z.json` "
      "into `app_settings.autoscalp_config`.")
    p("")
    p("## Frozen pre-change baseline")
    p("")
    b = BASELINE
    p(f"- window: {b['window']}")
    p(f"- {b['decided']} decided · WR {b['win_rate']}% · expectancy **{b['expectancy_pt']:+.2f} pt** · "
      f"net {b['net_pt']:+.1f} pt · PF {b['profit_factor']} · maxDD {b['max_dd_pt']} pt")
    p(f"- SUPPORT_BREAKDOWN net +94.6 (PF 2.75) · SUPPORT_REVERSAL net -43.0 · RESISTANCE_REVERSAL net -47.4")
    p("")
    p("## Forward (trades opened after cutoff)")
    p("")
    if not fwd:
        p("_No AUTOSCALP trades closed after the cutoff yet. Re-run after the next session._")
    else:
        p("```")
        p(f"ALL forward                {_fmt(_metrics(fwd))}")
        p(f"  4 blocked symbols        {_fmt(_metrics([r for r in fwd if r['underlying'] in TARGETS]))}")
        p(f"  NIFTY (control)          {_fmt(_metrics([r for r in fwd if r['underlying'] == 'NIFTY']))}")
        p("")
        for s in ("SUPPORT_BREAKDOWN", "SUPPORT_REVERSAL", "RESISTANCE_REVERSAL"):
            p(f"  setup={s:20s} {_fmt(_metrics([r for r in fwd if r['setup'] == s]))}")
        p("")
        for sym in sorted(set(r["underlying"] for r in fwd)):
            p(f"  {sym:20s}    {_fmt(_metrics([r for r in fwd if r['underlying'] == sym]))}")
        p("```")
        p("")
        # leak check: any blocked setup slipping through on a target symbol?
        leaks = [r for r in fwd if r["underlying"] in TARGETS and r["setup"] in BLOCKED_SETUPS]
        p(f"**Leak check** — blocked setups that still opened on a target symbol: "
          f"**{len(leaks)}**" + (" ✅" if not leaks else " ⚠️"))
        for r in leaks:
            p(f"  - {r['trade_id']} {r['underlying']} {r['setup']} @ {r['opened_ts']}  pnl={r['pnl']}")
        p("")
        # per-session forward
        p("### Forward by session")
        p("```")
        bysess = {}
        for r in fwd:
            d = datetime.fromisoformat(r["opened_ts"]).astimezone(IST).strftime("%Y-%m-%d")
            bysess.setdefault(d, []).append(r)
        for d in sorted(bysess):
            p(f"  {d}   {_fmt(_metrics(bysess[d]))}")
        p("```")
    p("")
    p("## Verdict gate")
    p("")
    p("Graduate the block from **ACCEPT-weak** to **VALIDATED** when, on the 4 blocked symbols, "
      "forward `SUPPORT_BREAKDOWN` holds **PF > 1.5** over **~3+ sessions / ~40+ trades**, "
      "the leak count stays 0, and NIFTY (control) shows no divergent regime shift that would "
      "explain the change. Reject / rollback if forward expectancy on the blocked symbols is "
      "worse than the −0.43 pt/trade pre-change MCX baseline over the same horizon.")

    txt = "\n".join(L) + "\n"
    OUT.write_text(txt)
    print(f"wrote {OUT}")
    if "--print" in sys.argv:
        print("\n" + txt)


if __name__ == "__main__":
    main()
