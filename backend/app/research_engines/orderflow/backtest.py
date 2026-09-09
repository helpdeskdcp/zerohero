"""
ORDERFLOW_ENGINE v1 -- NIFTY -- walk-forward backtest + GO/NO-GO.

Pipeline per the spec: Market Data -> order-flow features -> RK Score ->
Signal Direction -> Entry Timing Engine (two-stage) -> RR validation ->
Final Entry -> Exit Engine.  Calibration split TRAIN -> VALIDATION -> OOS;
the optimal retracement zone is fitted on TRAIN, confirmed on VALIDATION,
OOS scored once.

    python -m app.research_engines.orderflow.backtest \
        --tf 5 --start 2016-01-01 --end 2025-12-31 --out data/research/orderflow

RESEARCH ONLY. No broker, no live wiring, no order path. live_trading untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from . import data as D
from . import orderflow as OF
from . import signal as SG
from . import entry_timing as ET
from . import exit_engine as EX
from . import calibrate as CAL
from .config import merged


def _pt(trades: list[dict]) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0}
    wins = [t for t in trades if t["win"]]
    R = [t["r_multiple"] for t in trades]
    gl = sum(t["points"] for t in trades if t["points"] <= 0)
    gw = sum(t["points"] for t in trades if t["points"] > 0)
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for t in trades:
        eq += t["points"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {
        "n": n, "wins": len(wins), "losses": n - len(wins),
        "win_rate": round(len(wins) / n, 4),
        "expectancy_R": round(sum(R) / n, 4),
        "net_points": round(sum(t["points"] for t in trades), 1),
        "avg_win_points": round(gw / len(wins), 2) if wins else 0.0,
        "avg_loss_points": round(gl / (n - len(wins)), 2) if n - len(wins) else 0.0,
        "profit_factor": round(gw / abs(gl), 3) if gl < 0 else 9.99,
        "max_drawdown_points": round(dd, 1),
        "sl_rate": round(sum(t["sl_rate"] for t in trades) / n, 4),
        "t1_rate": round(sum(1 for t in trades if t["t1_hit"]) / n, 4),
        "t2_rate": round(sum(1 for t in trades if t["t2_hit"]) / n, 4),
        "t3_rate": round(sum(1 for t in trades if t["t3_hit"]) / n, 4),
        "avg_entry_quality": round(sum(t["entry_quality"] for t in trades) / n, 1),
        "avg_bars_waited": round(sum(t["bars_waited"] for t in trades) / n, 2),
    }


def _learning_record(setup: dict, ex: dict, sig: dict, cap: dict) -> dict:
    """The spec 'store every signal and subsequent price path' row."""
    return {
        "session_date": setup["session_date"], "entry_hhmm": setup["entry_hhmm"],
        "direction": setup["direction"],
        "signal_price": round(sig["close"], 2),
        "signal_high": setup["signal_high"], "signal_low": setup["signal_low"],
        "signal_range": setup["signal_range"],
        "retracement_zone_frac": setup["zone_frac"],
        "retest_overshoot_frac": setup["retest_overshoot_frac"],
        "whipsaws": setup["whipsaws"], "momentum_recovery": setup["momentum_recovery"],
        "bars_waited": setup["bars_waited"],
        "entry_price": setup["entry"], "stop_loss": setup["stop_loss"],
        "risk_points": setup["risk_points"],
        "t1": setup["t1"], "t2": setup["t2"], "t3": setup["t3"], "rr": setup["rr"],
        "mfe_R": ex["mfe_R"], "mae_R": ex["mae_R"],
        "t1_hit": ex["t1_hit"], "t2_hit": ex["t2_hit"], "t3_hit": ex["t3_hit"],
        "outcome": "WIN" if ex["win"] else "LOSS",
        "points": ex["points"], "r_multiple": ex["r_multiple"],
        "exit_reason": ex["exit_reason"],
        "time_to_t1": ex["time_to_t1"], "time_to_sl": ex["time_to_sl"],
        "held_bars": ex["held_bars"],
        "market_regime": f"{setup['regime_trend']}/{setup['regime_vol']}",
        "structure_state": setup["structure_state"],
        "vwap_dev_atr": setup["vwap_dev_atr"],
        "rk_score": sig["rk_score"], "rk_tilt_score": sig.get("rk_tilt_score"),
        "orderflow_method": cap["note"][:24] + " ...(PROXY)",
        "entry_quality_score": setup["entry_quality"],
    }


def run(tf_min=5, start="2016-01-01", end="2025-12-31",
        out="data/research/orderflow", config=None) -> dict:
    cfg = merged({**(config or {}), "tf_min": tf_min})
    t0 = time.time()
    bars, cap = D.load("NIFTY", tf_min=tf_min, start=start, end=end)
    rep = {
        "engine": "orderflow_v1_nifty", "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {"tf_min": tf_min, "start": start, "end": end},
        "capability": cap, "config": cfg,
    }
    if cap["n_sessions"] < 120:
        rep["status"] = "INSUFFICIENT_SAMPLE"
        rep["runtime_seconds"] = round(time.time() - t0, 1)
        _write(rep, out)
        return rep

    frame = OF.build(bars, cfg)
    sig_rows = SG.detect(frame, cfg)
    signals = [s for s in sig_rows if s["fresh_signal"]]

    dates = sorted({b["session_date"] for b in bars})
    n = len(dates)
    i_tr = int(n * cfg["train_frac"])
    i_va = int(n * (cfg["train_frac"] + cfg["val_frac"]))
    train_s, val_s, oos_s = dates[:i_tr], dates[i_tr:i_va], dates[i_va:]

    cal = CAL.calibrate(bars, frame, signals, cfg, train_s, val_s)
    z = cal["chosen_zone"]
    rep["calibration"] = cal
    rep["splits"] = {"train": [train_s[0], train_s[-1], len(train_s)],
                     "val": [val_s[0], val_s[-1], len(val_s)],
                     "oos": [oos_s[0], oos_s[-1], len(oos_s)]}

    # apply z* everywhere, build trades + learning dataset
    all_tr, learn = [], []
    status_counts: dict = {}
    for s in signals:
        r = ET.run(bars, frame, s["i"], s["direction"], s["rk_score"], cfg, zone_frac=z)
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
        if r["status"] != "ENTRY_READY":
            continue
        if r["entry_quality"] < cfg["eq_min"]:
            status_counts["REJECTED_LOW_EQ"] = status_counts.get("REJECTED_LOW_EQ", 0) + 1
            continue
        ex = EX.simulate(bars, r, cfg)
        tr = {**r, **ex, "year": r["session_date"][:4]}
        all_tr.append(tr)
        learn.append(_learning_record(r, ex, s, cap))

    def split(name, ss):
        sss = set(ss)
        return _pt([t for t in all_tr if t["session_date"] in sss])

    by_year = {}
    for y in sorted({t["year"] for t in all_tr}):
        by_year[y] = _pt([t for t in all_tr if t["year"] == y])

    # walk-forward on OOS-style expanding folds over the whole timeline
    wf = []
    fold = max(1, len(dates) // (cfg["wf_folds"] + 1))
    for k in range(1, cfg["wf_folds"] + 1):
        te = dates[fold * k: fold * (k + 1)]
        if not te:
            break
        wf.append({"test": [te[0], te[-1]],
                   **_pt([t for t in all_tr if t["session_date"] in set(te)])})

    oos = split("oos", oos_s)
    yrs_pos = [y for y, m in by_year.items() if m.get("n", 0) >= 15 and m.get("net_points", -1) > 0]
    go = (oos.get("n", 0) >= cfg["min_trades_for_verdict"]
          and oos.get("net_points", -1) > 0
          and oos.get("profit_factor", 0) >= cfg["verdict_min_pf"]
          and len(yrs_pos) >= cfg["verdict_min_pos_years"])
    rep["status"] = "OK"
    rep["signals_total"] = len(signals)
    rep["entry_status_counts"] = status_counts
    rep["trades_total"] = len(all_tr)
    rep["metrics"] = {"all": _pt(all_tr), "train": split("train", train_s),
                      "val": split("val", val_s), "oos": oos}
    rep["by_year"] = by_year
    rep["walk_forward"] = wf
    rep["go_no_go"] = {
        "verdict": "GO" if go else "NO-GO",
        "note": ("Edge holds out-of-sample (net>0, PF>=1.3, multi-year) with the "
                 "TRAIN-calibrated retracement zone." if go else
                 "Do NOT wire to live. The two-stage order-flow-proxy + optimal-entry "
                 "engine does not clear OOS net / PF / multi-year with the calibrated "
                 "zone. Consistent with the spec's data-limitation verdict for a "
                 "cash-index proxy engine."),
        "oos": oos, "positive_years": yrs_pos, "chosen_zone": z,
    }
    rep["runtime_seconds"] = round(time.time() - t0, 1)
    files = _write(rep, out, learn)
    rep["_files"] = files
    return rep


def _write(rep: dict, out: str, learn: list | None = None) -> dict:
    os.makedirs(out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jp = os.path.join(out, f"orderflow_v1_backtest_{stamp}.json")
    mp = os.path.join(out, f"orderflow_v1_backtest_{stamp}.md")
    with open(jp, "w") as fh:
        json.dump(rep, fh, indent=2, default=str)
    with open(mp, "w") as fh:
        fh.write(_md(rep))
    files = {"json": jp, "markdown": mp}
    if learn is not None:
        lp = os.path.join(out, f"orderflow_v1_learning_{stamp}.jsonl")
        with open(lp, "w") as fh:
            for r in learn:
                fh.write(json.dumps(r, default=str) + "\n")
        files["learning_dataset"] = lp
    return files


def _md(r) -> str:
    L = [f"# ORDERFLOW_ENGINE v1 -- NIFTY -- backtest", "",
         f"_generated {r['generated_at']} · tf {r['params']['tf_min']}m · "
         f"{r['params']['start']}..{r['params']['end']} · runtime {r.get('runtime_seconds','?')}s_", ""]
    cap = r["capability"]
    L += ["## Data capability (spec §20)", "```", json.dumps(cap, indent=1), "```", ""]
    if r.get("status") != "OK":
        L += [f"## STATUS: {r.get('status')}", "", "Not enough sessions for a "
              "TRAIN/VAL/OOS split.", ""]
        return "\n".join(L)
    gg = r["go_no_go"]
    L += [f"## VERDICT: **{gg['verdict']}**", "", gg["note"], "",
          f"- chosen retracement zone z* = **{gg['chosen_zone']}** "
          f"(_{r['calibration']['reason']}_)",
          f"- splits: TRAIN {r['splits']['train'][0]}..{r['splits']['train'][1]} "
          f"({r['splits']['train'][2]}) · VAL {r['splits']['val'][2]} · OOS {r['splits']['oos'][2]} sessions",
          f"- signals {r['signals_total']} · entries {r['trades_total']} · "
          f"status {json.dumps(r['entry_status_counts'])}", ""]
    L += ["## Zone calibration (TRAIN)", "",
          "| z | fill% | n | win% | expR | PF | net pt | MAE_R | MFE_R | SL% | T1% | T2% | T3% |",
          "|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for z, d in r["calibration"]["train_table"].items():
        if not d.get("n"):
            L.append(f"| {z} | {d.get('fill_rate')} | 0 | | | | | | | | | | |")
            continue
        L.append(f"| {z} | {d['fill_rate']} | {d['n']} | {round(d['win_rate']*100,1)} | "
                 f"{d['expectancy_R']} | {d['profit_factor']} | {d['net_points']} | {d['mae_R']} | "
                 f"{d['mfe_R']} | {round(d['sl_rate']*100,1)} | {round(d['t1_rate']*100,1)} | "
                 f"{round(d['t2_rate']*100,1)} | {round(d['t3_rate']*100,1)} |")
    L += ["", "## Zone calibration (VALIDATION)", "",
          "| z | n | win% | expR | PF | net pt |", "|--:|--:|--:|--:|--:|--:|"]
    for z, d in r["calibration"]["val_table"].items():
        if not d.get("n"):
            continue
        L.append(f"| {z} | {d['n']} | {round(d['win_rate']*100,1)} | {d['expectancy_R']} | "
                 f"{d['profit_factor']} | {d['net_points']} |")
    L += ["", "## Performance with z* (spec: two-stage, entry-quality gated)", "",
          "| split | n | W | L | win% | expR | PF | net pt | maxDD | SL% | T1% | T3% | avgEQ | avgWait |",
          "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for k in ("all", "train", "val", "oos"):
        m = r["metrics"][k]
        if not m.get("n"):
            L.append(f"| {k} | 0 | | | | | | | | | | | | |")
            continue
        L.append(f"| {k} | {m['n']} | {m['wins']} | {m['losses']} | {round(m['win_rate']*100,1)} | "
                 f"{m['expectancy_R']} | {m['profit_factor']} | {m['net_points']} | "
                 f"{m['max_drawdown_points']} | {round(m['sl_rate']*100,1)} | "
                 f"{round(m['t1_rate']*100,1)} | {round(m['t3_rate']*100,1)} | "
                 f"{m['avg_entry_quality']} | {m['avg_bars_waited']} |")
    L += ["", "## By year (net points)", "",
          "| year | n | win% | expR | PF | net pt |", "|--:|--:|--:|--:|--:|--:|"]
    for y, m in r["by_year"].items():
        if not m.get("n"):
            continue
        L.append(f"| {y} | {m['n']} | {round(m['win_rate']*100,1)} | {m['expectancy_R']} | "
                 f"{m['profit_factor']} | {m['net_points']} |")
    L += ["", "## Walk-forward (expanding folds, z* fixed)", "",
          "| fold | test | n | win% | expR | PF | net pt |", "|--|--|--:|--:|--:|--:|--:|"]
    for i, f in enumerate(r["walk_forward"], 1):
        if not f.get("n"):
            continue
        L.append(f"| {i} | {f['test'][0]}..{f['test'][1]} | {f['n']} | "
                 f"{round(f['win_rate']*100,1)} | {f['expectancy_R']} | {f['profit_factor']} | "
                 f"{f['net_points']} |")
    L += ["", "_Learning dataset (every signal + path) written alongside as "
          "`orderflow_v1_learning_*.jsonl` for continuous zone re-calibration._"]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="ORDERFLOW_ENGINE v1 backtest (research only)")
    ap.add_argument("--tf", type=int, default=5)
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--out", default="data/research/orderflow")
    a = ap.parse_args(argv)
    rep = run(tf_min=a.tf, start=a.start, end=a.end, out=a.out)
    print("STATUS:", rep.get("status"))
    if rep.get("status") == "OK":
        gg = rep["go_no_go"]
        print("VERDICT:", gg["verdict"], "| z* =", gg["chosen_zone"])
        m = rep["metrics"]
        for k in ("all", "train", "val", "oos"):
            d = m[k]
            if d.get("n"):
                print(f"  {k:5s} n={d['n']} win={round(d['win_rate']*100,1)}% "
                      f"expR={d['expectancy_R']} PF={d['profit_factor']} net={d['net_points']}pt")
    print("files:", rep.get("_files"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
