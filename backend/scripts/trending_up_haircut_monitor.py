#!/usr/bin/env python3
"""
trending_up_haircut_monitor.py  --  READ-ONLY.

Tracks the effect of the TRENDING_UP score haircut (m=0.80, non-NIFTY,
applied 2026-09-07 ~17:25 IST via config -- see
CALIBRATION_TRENDING_UP_HAIRCUT_PROPOSAL.md section 6).

Compares pre- vs post-haircut TRENDING_UP behaviour and, for the post window,
splits the haircut symbols (NATURALGAS / CRUDEOIL / BANKNIFTY / SENSEX) from the
NIFTY control (NOT hair-cut). Re-run this over the next few sessions; nothing is
changed.

  venv/bin/python scripts/trending_up_haircut_monitor.py
"""
from __future__ import annotations

import json
import sqlite3
import statistics as st
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "chanakya.db"
CUTOVER = "2026-09-07T11:55:00"          # UTC; config POST ~17:25 IST
HAIRCUT_SYMS = {"NATURALGAS", "CRUDEOIL", "BANKNIFTY", "SENSEX"}


def _con():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def _rows(where, args=()):
    con = _con()
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT symbol,regime,signal_type,decision,signal_score,probability,ev_r,rr,"
            "outcome,points,resolved,created_ts,session_date "
            f"FROM scalp_signals WHERE {where} ORDER BY created_ts", args)]
    finally:
        con.close()


def _snap_rows(where, args=()):
    con = _con()
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT symbol,regime,signal_type,decision,signal_score,probability,ev_r,"
            "no_trade_reason_class,ts,session_date "
            f"FROM live_market_snapshots WHERE {where} ORDER BY ts", args)]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _agg(rows):
    n = len(rows)
    if not n:
        return {"n": 0}
    sc = [r["signal_score"] for r in rows if r["signal_score"] is not None]
    pr = [r["probability"] for r in rows if r["probability"] is not None]
    ev = [r["ev_r"] for r in rows if r["ev_r"] is not None]
    res = [r for r in rows if r["resolved"] and r["outcome"] in ("WIN", "LOSS", "FLAT")]
    out = {
        "n": n, "buy": sum(1 for r in rows if r["decision"] in ("BUY_CE", "BUY_PE")),
        "avg_score": round(st.fmean(sc), 1) if sc else None,
        "avg_pred_prob": round(st.fmean(pr), 3) if pr else None,
        "avg_ev_r": round(st.fmean(ev), 3) if ev else None,
        "resolved": len(res),
    }
    if res:
        w = sum(1 for r in res if r["outcome"] == "WIN")
        pts = [r["points"] for r in res if r["points"] is not None]
        out.update({
            "win_rate": round(w / len(res), 3),
            "calib_gap_pp": round((st.fmean(r["probability"] for r in res) - w / len(res)) * 100, 1),
            "net_points": round(sum(pts), 1) if pts else None,
            "exp_points": round(st.fmean(pts), 2) if pts else None,
        })
    return out


def _line(tag, m):
    if not m.get("n"):
        return f"  {tag:<34} n=0"
    s = (f"  {tag:<34} n={m['n']:>3} buy={m['buy']:>3}  score~{m['avg_score']}  "
         f"pred~{m['avg_pred_prob']}  ev_r~{m['avg_ev_r']}  resolved={m['resolved']}")
    if m.get("resolved"):
        s += (f"  win={m['win_rate']*100:.0f}%  gap={m['calib_gap_pp']:+.1f}pp  "
              f"netPts={m['net_points']}  exp={m['exp_points']}")
    return s


def main():
    P = print
    P("=" * 100)
    P("TRENDING_UP HAIRCUT MONITOR  (m=0.80 non-NIFTY, applied 2026-09-07 ~17:25 IST)  [READ-ONLY]")
    P("=" * 100)

    pre = _rows("source='LIVE' AND status='CLOSED' AND regime='TRENDING_UP' "
                "AND probability IS NOT NULL AND created_ts <= ?", (CUTOVER,))
    post = _rows("source='LIVE' AND regime='TRENDING_UP' AND created_ts > ?", (CUTOVER,))

    P(f"\n[1] BASELINE  --  pre-haircut TRENDING_UP (<= {CUTOVER}Z)")
    P(_line("all symbols", _agg(pre)))
    P(_line("  haircut symbols (NG/CRUDE/BNF/SNSX)",
            _agg([r for r in pre if r["symbol"] in HAIRCUT_SYMS])))
    P(_line("  NIFTY (control)", _agg([r for r in pre if r["symbol"] == "NIFTY"])))
    if pre:
        bs = [r for r in pre if r["symbol"] in HAIRCUT_SYMS and r["signal_score"]]
        if bs:
            P(f"      haircut-symbol pre score: mean {st.fmean(x['signal_score'] for x in bs):.1f}  "
              f"-> an equivalent post setup should score ~{st.fmean(x['signal_score'] for x in bs)*0.8:.1f} "
              f"(x0.80)")

    P(f"\n[2] POST-HAIRCUT  --  TRENDING_UP signals after {CUTOVER}Z")
    if not post:
        P("  (none yet -- re-run after the next session(s). MCX symbols can still generate tonight.)")
    else:
        P(_line("haircut symbols", _agg([r for r in post if r["symbol"] in HAIRCUT_SYMS])))
        P(_line("NIFTY (control, NOT hair-cut)", _agg([r for r in post if r["symbol"] == "NIFTY"])))
        for r in post:
            tag = "cut" if r["symbol"] in HAIRCUT_SYMS else "CTRL"
            st_ = (f"{r['outcome']}" if r["resolved"] else "open")
            P(f"    {r['session_date']} {r['symbol']:<11} {r['signal_type']:<20} [{tag}] "
              f"{r['decision']:<7} score={r['signal_score']} pred={r['probability']} ev_r={r['ev_r']} -> {st_}")

    P("\n[3] DECISION-FLIP CHECK  --  TRENDING_UP live_market_snapshots after cutover")
    P("    (did the haircut push any would-be BUY on a haircut symbol to NO_TRADE?)")
    snaps = _snap_rows("regime='TRENDING_UP' AND ts > ?", (CUTOVER,))
    if not snaps:
        P("    (no TRENDING_UP snapshots after cutover yet)")
    else:
        for s in snaps:
            tag = "cut" if s["symbol"] in HAIRCUT_SYMS else "CTRL"
            P(f"    {s['session_date']} {s['symbol']:<11} [{tag}] {s['decision']:<9} "
              f"score={s['signal_score']} pred={s['probability']} ev_r={s['ev_r']} "
              f"nrc={s['no_trade_reason_class']}")
        nt = [s for s in snaps if s["symbol"] in HAIRCUT_SYMS and s["decision"] == "NO_TRADE"
              and "ev" in str(s["no_trade_reason_class"] or "").lower()]
        P(f"    -> {len(nt)} haircut-symbol TRENDING_UP snapshot(s) are NO_TRADE via an EV/score gate")

    P("\n[4] VERDICT")
    resolved_post = [r for r in post if r["resolved"] and r["outcome"] in ("WIN", "LOSS", "FLAT")
                     and r["symbol"] in HAIRCUT_SYMS]
    if len(resolved_post) < 15:
        P(f"    TOO EARLY -- {len(resolved_post)} resolved post-haircut TRENDING_UP trades on haircut "
          f"symbols (need >= 15 for a re-measure). Baseline gap was +29.4pp / PF 0.50 / -1.31 pts.")
        P("    Keep re-running this over the next few sessions.")
    else:
        m = _agg(resolved_post)
        P(f"    n={m['resolved']}  win={m['win_rate']*100:.0f}%  gap={m['calib_gap_pp']:+.1f}pp  "
          f"exp={m['exp_points']} pts   (baseline +29.4pp / -1.31)")
        P("    If gap still large / regime still -EV -> escalate to Option C (per-regime EV-gate, "
          "needs code) or D (block). See CALIBRATION_TRENDING_UP_HAIRCUT_PROPOSAL.md section 6.")


if __name__ == "__main__":
    main()
