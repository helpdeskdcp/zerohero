"""
Inside-Bar 2m -- chronological replay + OOS robustness check + GO/NO-GO.

The strategy is 100% RULE-BASED (nothing fitted from data), so "out-of-sample"
here = hold out the most recent slice of sessions and confirm the edge is
present there AND stable across calendar years -- the honest robustness test
for a fixed rule set.

    python -m app.research_engines._archived.inside_bar.backtest \
        --symbols NIFTY,BANKNIFTY --start 2015-01-01 --end 2025-12-31 \
        --out data/research/inside_bar

RESEARCH ONLY. No broker, no live wiring, no order path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from . import data as D
from . import strategy as S
from .config import merged
from app.research_engines.order_pressure.metrics import trading as _trading_metrics


def _sessions(bars: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for b in bars:
        out.setdefault(b["session_date"], []).append(b)
    return out


def _mod(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def simulate_trade(bars: list[dict], setup: dict, cfg: dict) -> dict:
    """Bar-by-bar from the entry bar. Scale 1/3 at +1R, 1/3 at +2R, trail the
    runner (BE after +1R, then 1R steps) to a +4R cap. SL-before-target intrabar.
    Returns position-weighted R + points, MAE/MFE in R, exit reason."""
    c = cfg
    i0 = setup["entry_index"]
    side = setup["side"]
    entry = setup["entry"]
    r_pts = setup["r_points"]
    sgn = 1 if side == "LONG" else -1
    stop = setup["stop"]
    hard = c["hard_exit_ist"]

    remaining = 1.0
    realized_R = 0.0
    hw_R = 0.0                    # high-water favourable excursion in R
    mae_R = 0.0
    scale_levels = [lvl for lvl, _ in c["scale_out"]]
    scale_frac = {lvl: fr for lvl, fr in c["scale_out"]}
    done_levels = set()
    exit_reason = None
    held = 0

    for j in range(i0, len(bars)):
        b = bars[j]
        held = j - i0
        fav = (b["h"] - entry) * sgn if side == "LONG" else (entry - b["l"]) * sgn * -1
        fav_ex = max((b["h"] - entry) * sgn, (b["l"] - entry) * sgn)
        adv_ex = min((b["h"] - entry) * sgn, (b["l"] - entry) * sgn)
        cur_hi_R = fav_ex / r_pts
        hw_R = max(hw_R, cur_hi_R)
        mae_R = min(mae_R, adv_ex / r_pts)

        # --- stop hit intrabar (checked first) ---
        stop_hit = (b["l"] <= stop) if side == "LONG" else (b["h"] >= stop)
        if stop_hit:
            exit_px = stop
            realized_R += remaining * ((exit_px - entry) * sgn) / r_pts
            remaining = 0.0
            exit_reason = "STOP" if hw_R < 1.0 else ("BREAKEVEN" if hw_R < 2.0 else "TRAIL")
            break

        # --- scale-outs at fixed R levels (touched intrabar) ---
        for lvl in scale_levels:
            if lvl in done_levels:
                continue
            tgt = entry + sgn * lvl * r_pts
            touched = (b["h"] >= tgt) if side == "LONG" else (b["l"] <= tgt)
            if touched:
                fr = min(scale_frac[lvl], remaining)
                realized_R += fr * lvl
                remaining -= fr
                done_levels.add(lvl)

        # --- runner cap ---
        cap = entry + sgn * c["runner_cap_r"] * r_pts
        capped = (b["h"] >= cap) if side == "LONG" else (b["l"] <= cap)
        if capped and remaining > 0:
            realized_R += remaining * c["runner_cap_r"]
            remaining = 0.0
            exit_reason = "TARGET_4R"
            break

        # --- trail the stop AFTER +1R ---
        if hw_R >= c["trail_after_r"]:
            new_stop_R = max(0.0, (hw_R // c["trail_step_r"]) * c["trail_step_r"] - c["trail_step_r"])
            # +1R reached -> BE (0R); +2R -> +1R; +3R -> +2R ...
            be_R = 0.0 if hw_R < 2.0 else (hw_R // 1.0 - 1.0)
            trail_stop = entry + sgn * be_R * r_pts
            stop = max(stop, trail_stop) if side == "LONG" else min(stop, trail_stop)

        # --- hard session exit ---
        if _mod(b["hhmm"]) >= _mod(hard) and remaining > 0:
            exit_px = b["c"]
            realized_R += remaining * ((exit_px - entry) * sgn) / r_pts
            remaining = 0.0
            exit_reason = "TIME_1PM"
            break

    if remaining > 0:
        exit_px = bars[-1]["c"]
        realized_R += remaining * ((exit_px - entry) * sgn) / r_pts
        exit_reason = exit_reason or "EOD"

    realized_R -= c["cost_r"]
    return {
        "session_date": setup["session_date"], "side": side,
        "entry": entry, "stop": setup["stop"], "r_points": r_pts,
        "entry_hhmm": setup["entry_hhmm"], "held_bars": held,
        "r_multiple": round(realized_R, 4), "points": round(realized_R * r_pts, 3),
        "win": realized_R > 0, "mae": round(mae_R, 3), "mfe": round(hw_R, 3),
        "exit_reason": exit_reason,
    }


def run_symbol(symbol: str, start: str, end: str, cfg: dict) -> dict:
    bars = D.load_2m(symbol, start=start, end=end)
    sess = _sessions(bars)
    dates = sorted(sess)
    if len(dates) < 40:
        return {"status": "INSUFFICIENT_SAMPLE", "n_sessions": len(dates),
                "n_2m_bars": len(bars)}
    trades: list[dict] = []
    setups_total = 0
    for d in dates:
        day = sess[d]
        setups = S.find_setups(day, cfg)
        setups_total += len(setups)
        taken = 0
        last_exit_idx = -1
        for su in setups:
            if taken >= cfg["max_trades_per_day"]:
                break
            if su["entry_index"] <= last_exit_idx:      # one position at a time
                continue
            tr = simulate_trade(day, su, cfg)
            trades.append(tr)
            taken += 1
            last_exit_idx = su["entry_index"] + tr["held_bars"]

    if not trades:
        return {"status": "NO_TRADES", "n_sessions": len(dates), "setups_seen": setups_total}

    def yr(t):
        return t["session_date"][:4]

    all_m = _trading_metrics(trades)
    # OOS tail = most recent ~30% of sessions
    cut = dates[int(len(dates) * 0.7)]
    oos = [t for t in trades if t["session_date"] >= cut]
    ins = [t for t in trades if t["session_date"] < cut]
    by_year = {}
    for y in sorted({yr(t) for t in trades}):
        by_year[y] = _trading_metrics([t for t in trades if yr(t) == y])
    by_side = {s: _trading_metrics([t for t in trades if t["side"] == s]) for s in ("LONG", "SHORT")}
    by_exit = {}
    for t in trades:
        by_exit.setdefault(t["exit_reason"], []).append(t["r_multiple"])
    by_exit = {k: {"n": len(v), "sum_R": round(sum(v), 2)} for k, v in by_exit.items()}

    return {
        "status": "OK",
        "n_sessions": len(dates), "date_range": [dates[0], dates[-1]],
        "setups_triggered": setups_total, "trades_taken": len(trades),
        "trades_per_day": round(len(trades) / len(dates), 2),
        "all": all_m,
        "in_sample": _trading_metrics(ins),
        "out_of_sample": {**_trading_metrics(oos), "since": cut, "n_sessions_oos": len(dates) - int(len(dates) * 0.7)},
        "by_year": by_year,
        "by_side": by_side,
        "by_exit_reason": by_exit,
        "target_r_hit_rate": _r_hit_rates(trades, cfg),
    }


def _r_hit_rates(trades, cfg):
    n = len(trades)
    return {f"reached_{lvl}R": round(sum(1 for t in trades if t["mfe"] >= lvl) / n, 3)
            for lvl in (1, 2, 3, 4)} | {"stopped_pre_1R": round(
                sum(1 for t in trades if t["mfe"] < 1.0 and not t["win"]) / n, 3)}


def _verdict(per_symbol: dict, cfg: dict) -> dict:
    reasons = []
    passing = []
    for sym, r in per_symbol.items():
        if r.get("status") != "OK":
            reasons.append(f"{sym}: {r.get('status')}")
            continue
        oos = r["out_of_sample"]
        if oos["n"] < cfg["min_trades_for_verdict"]:
            reasons.append(f"{sym}: only {oos['n']} OOS trades (<{cfg['min_trades_for_verdict']})")
            continue
        exp = oos.get("expectancy_R", -9)
        pf = oos.get("profit_factor", 0)
        dd = oos.get("max_drawdown_R", -999)
        yrs_pos = [y for y, m in r["by_year"].items()
                   if m.get("n", 0) >= 20 and m.get("expectancy_R", -9) > 0]
        ok = exp > cfg["cost_r"] and pf >= 1.3 and len(yrs_pos) >= 2
        if ok:
            passing.append(sym)
        else:
            if exp <= cfg["cost_r"]:
                reasons.append(f"{sym}: OOS expectancy {exp}R not > cost")
            if pf < 1.3:
                reasons.append(f"{sym}: OOS profit factor {pf} < 1.3")
            if len(yrs_pos) < 2:
                reasons.append(f"{sym}: positive in only {len(yrs_pos)} calendar year(s) (<2)")
    go = len(passing) >= 1 and not any(s in " ".join(reasons) for s in passing)
    return {
        "verdict": "GO" if go and passing else "NO-GO",
        "passing_symbols": passing,
        "failed": reasons,
        "note": ("Edge holds out-of-sample on the listed symbol(s)." if (go and passing) else
                 "Do NOT wire into production. On out-of-sample sessions the inside-bar "
                 "2m rule set does not clear the expectancy / PF / multi-year-stability bar. "
                 "Consistent with the other strategy audits this session."),
    }


def run(symbols=("NIFTY", "BANKNIFTY", "SENSEX"), start="2015-01-01", end=None,
        out="data/research/inside_bar", config=None) -> dict:
    cfg = merged(config)
    t0 = time.time()
    rep = {
        "engine": "inside_bar_2m",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {"symbols": list(symbols), "start": start, "end": end, "config": cfg},
        "coverage": D.coverage(),
        "by_symbol": {},
    }
    for sym in symbols:
        rep["by_symbol"][sym] = run_symbol(sym, start, end, cfg)
    rep["go_no_go"] = _verdict(rep["by_symbol"], cfg)
    rep["runtime_seconds"] = round(time.time() - t0, 1)

    os.makedirs(out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jp = os.path.join(out, f"inside_bar_backtest_{stamp}.json")
    with open(jp, "w") as fh:
        json.dump(rep, fh, indent=2, default=str)
    mp = os.path.join(out, f"inside_bar_backtest_{stamp}.md")
    with open(mp, "w") as fh:
        fh.write(_md(rep))
    rep["_files"] = {"json": jp, "markdown": mp}
    return rep


def _md(r) -> str:
    L = [f"# Inside-Bar 2m strategy -- backtest", "",
         f"_generated {r['generated_at']} · {', '.join(r['params']['symbols'])} · "
         f"{r['params']['start']}..{r['params']['end']} · runtime {r['runtime_seconds']}s_", "",
         f"## VERDICT: **{r['go_no_go']['verdict']}**", "", r['go_no_go']['note'], ""]
    if r['go_no_go']['failed']:
        L += ["Failed:"] + [f"- {x}" for x in r['go_no_go']['failed']] + [""]
    L += ["## Coverage", "```", json.dumps(r["coverage"]["by_symbol"], indent=1), "```",
          *[f"- {n}" for n in r["coverage"]["notes"]], ""]
    for sym, s in r["by_symbol"].items():
        L += ["", f"## {sym}", ""]
        if s.get("status") != "OK":
            L.append(f"`{s.get('status')}` ({s.get('n_sessions','?')} sessions, "
                     f"{s.get('setups_seen', s.get('n_2m_bars','?'))})")
            continue
        L.append(f"{s['n_sessions']} sessions {s['date_range'][0]}..{s['date_range'][1]} · "
                 f"{s['setups_triggered']} setups triggered · {s['trades_taken']} trades "
                 f"({s['trades_per_day']}/day)")
        for tag in ("all", "in_sample", "out_of_sample"):
            m = s[tag]
            L.append(f"- **{tag}** n={m.get('n')} win={m.get('win_rate')} "
                     f"expR={m.get('expectancy_R')} PF={m.get('profit_factor')} "
                     f"maxDD={m.get('max_drawdown_R')}R avgW={m.get('avg_win_R')} avgL={m.get('avg_loss_R')}")
        L += ["", "| year | n | win | expR | PF | maxDD R |", "|---|--:|--:|--:|--:|--:|"]
        for y, m in s["by_year"].items():
            L.append(f"| {y} | {m.get('n')} | {m.get('win_rate')} | {m.get('expectancy_R')} | "
                     f"{m.get('profit_factor')} | {m.get('max_drawdown_R')} |")
        L += ["", f"by side: LONG {s['by_side']['LONG']} · SHORT {s['by_side']['SHORT']}",
              f"target-R hit rates: {json.dumps(s['target_r_hit_rate'])}",
              f"exit reasons: {json.dumps(s['by_exit_reason'])}"]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Inside-Bar 2m backtest (research only)")
    ap.add_argument("--symbols", default="NIFTY,BANKNIFTY,SENSEX")
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", default="data/research/inside_bar")
    ap.add_argument("--coverage-only", action="store_true")
    a = ap.parse_args(argv)
    if a.coverage_only:
        print(json.dumps(D.coverage(), indent=2)); return 0
    rep = run(symbols=tuple(a.symbols.split(",")), start=a.start, end=a.end, out=a.out)
    print("\n==== VERDICT:", rep["go_no_go"]["verdict"], "====")
    for x in rep["go_no_go"]["failed"]:
        print("  -", x)
    print("files:", rep["_files"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
