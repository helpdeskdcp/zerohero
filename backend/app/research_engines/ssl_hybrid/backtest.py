"""
SSL Hybrid PRO -- chronological replay + walk-forward OOS + GO/NO-GO.
Reports WIN / LOSS in POINTS (and in R).

    python -m app.research_engines.ssl_hybrid.backtest \
        --symbols NIFTY,BANKNIFTY --tf 15 --start 2016-01-01 --end 2025-12-31 \
        --out data/research/ssl_hybrid

RESEARCH ONLY. No broker, no live wiring, no order path.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

from . import data as D
from . import strategy as S
from .config import merged


def _mod(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def simulate_trade(bars: list[dict], frame: list[dict], setup: dict, cfg: dict) -> dict:
    """Bar-by-bar from the entry bar. 1/3 off at T1 (then SL->BE), 1/3 at T2,
    last 1/3 at T3 / session end / opposite signal. SL-before-target intrabar.
    P&L in POINTS (position-weighted); also expressed in R."""
    i0 = setup["entry_index"]
    side = setup["side"]
    dirn = 1 if side == "LONG" else -1
    entry = setup["entry"]
    risk = setup["risk_points"]
    stop = setup["stop"]
    end_min = _mod(cfg["session_end_ist"])
    scale = [(lvl, fr) for lvl, fr in cfg["scale_out"]]
    be_after = cfg["move_sl_to_be_after_rr"]
    final_rr = cfg["final_target_rr"]

    remaining = 1.0
    pnl_pts = 0.0
    done = set()
    hw_R = 0.0
    mae_pts = 0.0
    exit_reason = None
    held = 0

    for j in range(i0, len(bars)):
        b = bars[j]
        held = j - i0
        fav = (b["h"] - entry) * dirn if side == "LONG" else (entry - b["l"]) * dirn
        adv = (b["l"] - entry) * dirn if side == "LONG" else (entry - b["h"]) * dirn
        hw_R = max(hw_R, ((max(b["h"], b["l"], key=lambda p: (p - entry) * dirn) - entry) * dirn) / risk)
        mae_pts = min(mae_pts, adv)

        # 1) stop first (intrabar)
        hit_stop = (b["l"] <= stop) if side == "LONG" else (b["h"] >= stop)
        if hit_stop:
            pnl_pts += remaining * (stop - entry) * dirn
            remaining = 0.0
            exit_reason = "STOP" if hw_R < 1.0 else ("BREAKEVEN" if hw_R < 2.0 else "TRAIL_BE")
            break

        # 2) scale targets (T1, T2)
        for lvl, fr in scale:
            if lvl in done:
                continue
            tgt = entry + dirn * lvl * risk
            touched = (b["h"] >= tgt) if side == "LONG" else (b["l"] <= tgt)
            if touched:
                take = min(fr, remaining)
                pnl_pts += take * (tgt - entry) * dirn
                remaining -= take
                done.add(lvl)
                if lvl >= be_after:
                    stop = max(stop, entry) if side == "LONG" else min(stop, entry)

        # 3) final target for the runner
        if remaining > 0:
            ft = entry + dirn * final_rr * risk
            hit = (b["h"] >= ft) if side == "LONG" else (b["l"] <= ft)
            if hit:
                pnl_pts += remaining * (ft - entry) * dirn
                remaining = 0.0
                exit_reason = "TARGET_T3"
                break

        # 4) opposite signal flattens at the close
        if cfg["exit_on_opposite_signal"] and remaining > 0:
            fr_row = frame[j]
            opp = fr_row["sell_signal"] if side == "LONG" else fr_row["buy_signal"]
            if opp:
                pnl_pts += remaining * (b["c"] - entry) * dirn
                remaining = 0.0
                exit_reason = "OPPOSITE"
                break

        # 5) session-end flat (VWAP is session-anchored -> intraday system)
        if cfg["hard_exit_session_end"] and b["minute_of_day"] >= end_min and remaining > 0:
            pnl_pts += remaining * (b["c"] - entry) * dirn
            remaining = 0.0
            exit_reason = "SESSION_END"
            break

    if remaining > 0:
        pnl_pts += remaining * (bars[-1]["c"] - entry) * dirn
        exit_reason = exit_reason or "EOD"

    pnl_pts -= cfg["cost_points"]
    return {
        "session_date": setup["session_date"], "side": side, "entry": entry,
        "stop": setup["stop"], "risk_points": risk, "entry_hhmm": setup["entry_hhmm"],
        "held_bars": held, "points": round(pnl_pts, 3),
        "r_multiple": round(pnl_pts / risk, 4) if risk else 0.0,
        "win": pnl_pts > 0.0, "mae_points": round(mae_pts, 2),
        "exit_reason": exit_reason,
    }


def _pt_metrics(trades: list[dict]) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0}
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    gw = sum(t["points"] for t in wins)
    gl = sum(t["points"] for t in losses)      # <= 0
    net = gw + gl
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for t in trades:
        eq += t["points"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    rs = [t["r_multiple"] for t in trades]
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n, 4),
        "net_points": round(net, 1), "gross_win_points": round(gw, 1),
        "gross_loss_points": round(gl, 1),
        "avg_win_points": round(gw / len(wins), 2) if wins else 0.0,
        "avg_loss_points": round(gl / len(losses), 2) if losses else 0.0,
        "expectancy_points": round(net / n, 3),
        "profit_factor": round(gw / abs(gl), 3) if gl < 0 else float("inf"),
        "max_drawdown_points": round(dd, 1),
        "expectancy_R": round(sum(rs) / n, 4),
        "avg_R": round(sum(rs) / n, 4),
    }


def run_symbol(symbol: str, start: str, end: str, cfg: dict) -> dict:
    bars = D.load_bars(symbol, tf_min=cfg["tf_min"], start=start, end=end)
    if not bars:
        return {"status": "NO_DATA"}
    dates = sorted({b["session_date"] for b in bars})
    if len(dates) < 60:
        return {"status": "INSUFFICIENT_SAMPLE", "n_sessions": len(dates), "n_bars": len(bars)}

    frame = S.build_signals(bars, cfg)
    n_signals = sum(1 for row in frame
                    if row["i"] >= cfg["warmup_bars"] and (row["buy_signal"] or row["sell_signal"]))
    setup_objs = S.find_setups(bars, cfg)

    trades = []
    last_exit = -1
    for su in setup_objs:
        if cfg["one_position_at_a_time"] and su["entry_index"] <= last_exit:
            continue
        tr = simulate_trade(bars, frame, su, cfg)
        trades.append(tr)
        last_exit = su["entry_index"] + tr["held_bars"]

    if not trades:
        return {"status": "NO_TRADES", "n_sessions": len(dates), "signals_seen": n_signals}

    def yr(t):
        return t["session_date"][:4]

    cut = dates[int(len(dates) * (1.0 - cfg["oos_frac"]))]
    ins = [t for t in trades if t["session_date"] < cut]
    oos = [t for t in trades if t["session_date"] >= cut]
    by_year = {y: _pt_metrics([t for t in trades if yr(t) == y])
               for y in sorted({yr(t) for t in trades})}
    by_side = {s: _pt_metrics([t for t in trades if t["side"] == s]) for s in ("LONG", "SHORT")}
    by_exit: dict = {}
    for t in trades:
        by_exit.setdefault(t["exit_reason"], []).append(t["points"])
    by_exit = {k: {"n": len(v), "sum_points": round(sum(v), 1)} for k, v in by_exit.items()}

    return {
        "status": "OK",
        "n_sessions": len(dates), "date_range": [dates[0], dates[-1]],
        "signals": n_signals, "trades": len(trades),
        "trades_per_session": round(len(trades) / len(dates), 3),
        "has_volume": bars[0]["has_volume"],
        "all": _pt_metrics(trades),
        "in_sample": _pt_metrics(ins),
        "out_of_sample": {**_pt_metrics(oos), "since": cut,
                          "n_sessions_oos": len(dates) - int(len(dates) * (1.0 - cfg["oos_frac"]))},
        "by_year": by_year, "by_side": by_side, "by_exit_reason": by_exit,
    }


def _verdict(per_symbol: dict, cfg: dict) -> dict:
    reasons, passing = [], []
    for sym, r in per_symbol.items():
        if r.get("status") != "OK":
            reasons.append(f"{sym}: {r.get('status')}")
            continue
        oos = r["out_of_sample"]
        if oos["n"] < cfg["min_trades_for_verdict"]:
            reasons.append(f"{sym}: only {oos['n']} OOS trades (<{cfg['min_trades_for_verdict']})")
            continue
        pf = oos.get("profit_factor", 0)
        net = oos.get("net_points", -1)
        yrs_pos = [y for y, m in r["by_year"].items()
                   if m.get("n", 0) >= 15 and m.get("net_points", -1) > 0]
        ok = net > 0 and pf >= cfg["verdict_min_pf"] and len(yrs_pos) >= cfg["verdict_min_pos_years"]
        if ok:
            passing.append(sym)
        else:
            if net <= 0:
                reasons.append(f"{sym}: OOS net {net} points (<=0)")
            if pf < cfg["verdict_min_pf"]:
                reasons.append(f"{sym}: OOS profit factor {pf} < {cfg['verdict_min_pf']}")
            if len(yrs_pos) < cfg["verdict_min_pos_years"]:
                reasons.append(f"{sym}: net-positive in only {len(yrs_pos)} year(s) "
                               f"(<{cfg['verdict_min_pos_years']})")
    return {
        "verdict": "GO" if passing else "NO-GO",
        "passing_symbols": passing, "failed": reasons,
        "note": ("Edge holds out-of-sample (points-positive, PF>=1.3, multi-year) on "
                 + ", ".join(passing) + "." if passing else
                 "Do NOT wire into production. On out-of-sample sessions the SSL Hybrid "
                 "PRO rule set does not clear the points / PF / multi-year bar."),
    }


def run(symbols=("NIFTY", "BANKNIFTY", "SENSEX"), tf_min=15, start="2016-01-01",
        end=None, out="data/research/ssl_hybrid", config=None) -> dict:
    cfg = merged({**(config or {}), "tf_min": tf_min})
    t0 = time.time()
    rep = {
        "engine": "ssl_hybrid_pro", "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {"symbols": list(symbols), "tf_min": tf_min, "start": start, "end": end,
                   "config": cfg},
        "coverage": D.coverage(tf_min),
        "by_symbol": {},
    }
    for sym in symbols:
        rep["by_symbol"][sym] = run_symbol(sym, start, end, cfg)
    rep["go_no_go"] = _verdict(rep["by_symbol"], cfg)
    rep["runtime_seconds"] = round(time.time() - t0, 1)

    os.makedirs(out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jp = os.path.join(out, f"ssl_hybrid_backtest_{stamp}.json")
    mp = os.path.join(out, f"ssl_hybrid_backtest_{stamp}.md")
    with open(jp, "w") as fh:
        json.dump(rep, fh, indent=2, default=str)
    with open(mp, "w") as fh:
        fh.write(_md(rep))
    rep["_files"] = {"json": jp, "markdown": mp}
    return rep


def _md(r) -> str:
    p = r["params"]
    L = [f"# SSL Hybrid PRO -- backtest (WIN / LOSS in points)", "",
         f"_generated {r['generated_at']} · {', '.join(p['symbols'])} · {p['tf_min']}m · "
         f"{p['start']}..{p['end']} · runtime {r['runtime_seconds']}s_", "",
         f"## VERDICT: **{r['go_no_go']['verdict']}**", "", r["go_no_go"]["note"], ""]
    if r["go_no_go"]["failed"]:
        L += ["Failed:"] + [f"- {x}" for x in r["go_no_go"]["failed"]] + [""]
    L += ["## Coverage", "```", json.dumps(r["coverage"]["by_symbol"], indent=1), "```",
          *[f"- {n}" for n in r["coverage"]["notes"]], ""]
    for sym, s in r["by_symbol"].items():
        L += ["", f"## {sym}", ""]
        if s.get("status") != "OK":
            L.append(f"`{s.get('status')}`" + (f" ({s.get('n_sessions','?')} sessions)"
                     if s.get("n_sessions") else ""))
            continue
        L.append(f"{s['n_sessions']} sessions {s['date_range'][0]}..{s['date_range'][1]} · "
                 f"{s['signals']} signals · {s['trades']} trades ({s['trades_per_session']}/session) · "
                 f"has_volume={s['has_volume']}")
        L += ["", "| slice | n | W | L | win% | net pts | avg win | avg loss | PF | maxDD pts | exp R |",
              "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
        for tag in ("all", "in_sample", "out_of_sample"):
            m = s[tag]
            if not m.get("n"):
                L.append(f"| {tag} | 0 | | | | | | | | | |")
                continue
            L.append(f"| {tag} | {m['n']} | {m['wins']} | {m['losses']} | "
                     f"{round(m['win_rate']*100,1)} | {m['net_points']} | {m['avg_win_points']} | "
                     f"{m['avg_loss_points']} | {m['profit_factor']} | {m['max_drawdown_points']} | "
                     f"{m['expectancy_R']} |")
        L += ["", "| year | n | W | L | net pts | PF |", "|---|--:|--:|--:|--:|--:|"]
        for y, m in s["by_year"].items():
            if not m.get("n"):
                continue
            L.append(f"| {y} | {m['n']} | {m['wins']} | {m['losses']} | {m['net_points']} | "
                     f"{m['profit_factor']} |")
        L += ["", f"by side: LONG net {s['by_side']['LONG'].get('net_points',0)} pts "
              f"({s['by_side']['LONG'].get('n',0)}) · "
              f"SHORT net {s['by_side']['SHORT'].get('net_points',0)} pts "
              f"({s['by_side']['SHORT'].get('n',0)})",
              f"exit reasons: {json.dumps(s['by_exit_reason'])}"]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="SSL Hybrid PRO backtest (research only)")
    ap.add_argument("--symbols", default="NIFTY,BANKNIFTY,SENSEX")
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", default="data/research/ssl_hybrid")
    ap.add_argument("--coverage-only", action="store_true")
    a = ap.parse_args(argv)
    if a.coverage_only:
        print(json.dumps(D.coverage(a.tf), indent=2))
        return 0
    rep = run(symbols=tuple(a.symbols.split(",")), tf_min=a.tf, start=a.start, end=a.end, out=a.out)
    gg = rep["go_no_go"]
    print("\n==== VERDICT:", gg["verdict"], "====")
    for x in gg["failed"]:
        print("  -", x)
    for sym, s in rep["by_symbol"].items():
        if s.get("status") != "OK":
            print(f"{sym}: {s.get('status')}")
            continue
        a_, o_ = s["all"], s["out_of_sample"]
        print(f"{sym}: {s['trades']} trades | ALL net {a_['net_points']} pts "
              f"(W{a_['wins']}/L{a_['losses']}, PF {a_['profit_factor']}) | "
              f"OOS net {o_['net_points']} pts (W{o_.get('wins')}/L{o_.get('losses')}, "
              f"PF {o_.get('profit_factor')})")
    print("files:", rep["_files"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
