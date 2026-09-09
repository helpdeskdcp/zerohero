"""
TRI-COMPARE orchestrator: run Engine 1 / 2 / 3 on the SAME NIFTY data through the
SAME harness, with + without the ANN confirmation layer, split TRAIN -> VAL ->
OOS chronologically, walk-forward, and produce one unified comparison report +
a per-engine GO/NO-GO + a signal-qualification rule.

    python -m app.research_engines.tri_compare.compare \
        --tf 15 --start 2016-01-01 --end 2025-12-31 --out data/research/tri_compare
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from ..orderflow import data as D
from . import features as FE
from . import engine_trend as E1
from . import engine_structure as E2
from . import engine_hybrid as E3
from . import harness as H
from . import metrics as M
from .ann_layer import AnnConfirm
from .config import merged


def _split_dates(dates: list[str], cfg: dict):
    n = len(dates)
    a = int(n * cfg["train_frac"])
    b = int(n * (cfg["train_frac"] + cfg["val_frac"]))
    return set(dates[:a]), set(dates[a:b]), set(dates[b:]), (dates[a - 1], dates[b - 1])


def _engine_signals(name, frame, bars, cfg):
    if name == "E1_TREND":
        return E1.signals(frame, cfg)
    if name == "E2_STRUCTURE":
        return E2.signals(frame, bars, cfg, e_key="e2")
    if name == "E3_HYBRID":
        return E3.signals(frame, bars, cfg)
    raise ValueError(name)


def _walk_forward(trades: list[dict], cfg: dict) -> list[dict]:
    """Expanding-window folds; rules are frozen, so WF re-fits ONLY the ANN per
    fold on that fold's train slice and reports A vs B on the fold test slice."""
    dates = sorted({t["session_date"] for t in trades})
    if len(dates) < cfg["wf_folds"] + 2:
        return []
    fold = len(dates) // (cfg["wf_folds"] + 1)
    out = []
    for k in range(1, cfg["wf_folds"] + 1):
        tr_end = dates[fold * k]
        te = set(dates[fold * k: fold * (k + 1)])
        if not te:
            break
        tr = [t for t in trades if t["session_date"] < tr_end]
        ts = [t for t in trades if t["session_date"] in te]
        ann = AnnConfirm(cfg["ann"]).fit(tr) if cfg["ann"]["enabled"] else None
        b = ann.filter(ts) if (ann and ann.status == "OK") else ts
        out.append({
            "fold": k, "test": [min(te), max(te)],
            "A": M.summarize(ts), "B": M.summarize(b),
            "ann_status": ann.status if ann else "OFF",
        })
    return out


def run(tf_min=15, start="2016-01-01", end="2025-12-31",
        out="data/research/tri_compare", config=None) -> dict:
    cfg = merged({**(config or {}), "tf_min": tf_min, "start": start, "end": end})
    t0 = time.time()
    bars, cap = D.load(cfg["symbol"], tf_min=tf_min, start=start, end=end)
    rep = {
        "framework": "tri_compare", "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {"symbol": cfg["symbol"], "tf_min": tf_min, "start": start, "end": end},
        "data_capability": cap, "config": cfg, "engines": {},
    }
    if cap["n_sessions"] < 200:
        rep["status"] = "INSUFFICIENT_SAMPLE"
        _write(rep, out)
        return rep

    frame = FE.build_frame(bars, cfg)
    dates = sorted({b["session_date"] for b in bars})
    train_s, val_s, oos_s, cut = _split_dates(dates, cfg)
    rep["splits"] = {"train": [dates[0], cut[0], len(train_s)],
                     "val": [cut[0], cut[1], len(val_s)],
                     "oos": [cut[1], dates[-1], len(oos_s)]}

    for name in ("E1_TREND", "E2_STRUCTURE", "E3_HYBRID"):
        sigs = _engine_signals(name, frame, bars, cfg)
        h = H.run_engine(name, sigs, bars, frame, cfg)
        tr = h["trades"]
        tr_train = [t for t in tr if t["session_date"] in train_s]
        tr_val = [t for t in tr if t["session_date"] in val_s]
        tr_oos = [t for t in tr if t["session_date"] in oos_s]

        ann = AnnConfirm(cfg["ann"]).fit(tr_train) if cfg["ann"]["enabled"] else None
        b_val = ann.filter(tr_val) if (ann and ann.status == "OK") else tr_val
        b_oos = ann.filter(tr_oos) if (ann and ann.status == "OK") else tr_oos

        reg_oos = M.by_key(tr_oos, lambda t: f'{t["regime_trend"]}/{t["regime_vol"]}')
        A = {"all": M.summarize(tr), "train": M.summarize(tr_train),
             "val": M.summarize(tr_val), "oos": M.summarize(tr_oos)}
        B = {"val": M.summarize(b_val), "oos": M.summarize(b_oos)}
        comp = M.composite(A["oos"], A["train"], reg_oos, cfg["composite_weights"], cfg["min_trades_for_verdict"])
        rep["engines"][name] = {
            "signals_total": h["signals_total"], "ready_total": h["ready_total"],
            "entries_total": h["entries_total"], "harness_rejects": h["rejected"],
            "A_engine_only": A, "B_engine_plus_ann": B,
            "ann": ann.info() if ann else {"status": "OFF"},
            "by_regime_oos": reg_oos,
            "by_hour_oos": M.by_key(tr_oos, M.hour_bucket),
            "by_side_oos": M.by_key(tr_oos, lambda t: t["direction"]),
            "by_year": M.by_key(tr, lambda t: t["year"]),
            "walk_forward": _walk_forward(tr, cfg),
            "composite_oos": comp,
            "verdict": _verdict(A, reg_oos, tr, cfg),
        }

    rep["comparison"] = _comparison(rep["engines"], cfg)
    rep["runtime_seconds"] = round(time.time() - t0, 1)
    rep["status"] = "OK"
    rep["_files"] = _write(rep, out)
    return rep


def _verdict(A: dict, reg_oos: dict, all_tr: list[dict], cfg: dict) -> dict:
    o, tr = A["oos"], A["train"]
    fail = []
    if o.get("n", 0) < cfg["min_trades_for_verdict"]:
        fail.append(f"OOS n {o.get('n',0)} < {cfg['min_trades_for_verdict']}")
    if o.get("expectancy_R", -9) <= 0:
        fail.append(f"OOS expectancy {o.get('expectancy_R')} <= 0")
    if o.get("profit_factor", 0) < cfg["verdict_min_pf"]:
        fail.append(f"OOS PF {o.get('profit_factor')} < {cfg['verdict_min_pf']}")
    pos_reg = [k for k, m in reg_oos.items() if m.get("n", 0) >= 8 and m.get("expectancy_R", -9) > 0]
    if len(pos_reg) < cfg["verdict_min_pos_regimes"]:
        fail.append(f"positive in {len(pos_reg)} regime(s) < {cfg['verdict_min_pos_regimes']}")
    yrs = M.by_key(all_tr, lambda t: t["year"])
    pos_y = [y for y, m in yrs.items() if m.get("n", 0) >= 15 and m.get("net_points", -1) > 0]
    if len(pos_y) < cfg["verdict_min_pos_years"]:
        fail.append(f"net-positive in {len(pos_y)} year(s) < {cfg['verdict_min_pos_years']}")
    st = M.stability(tr, o)
    if (1.0 - st) > cfg["verdict_max_degradation"]:
        fail.append(f"TRAIN->OOS degradation {round(1-st,2)} > {cfg['verdict_max_degradation']}")
    return {"verdict": "GO" if not fail else "NO-GO", "failed": fail,
            "oos_expectancy_R": o.get("expectancy_R"), "oos_pf": o.get("profit_factor"),
            "positive_regimes": pos_reg, "positive_years": pos_y, "stability": st}


def _comparison(engines: dict, cfg: dict) -> dict:
    rows = []
    for name, e in engines.items():
        o = e["A_engine_only"]["oos"]
        bo = e["B_engine_plus_ann"]["oos"]
        ann_delta = (round(bo.get("expectancy_R", 0) - o.get("expectancy_R", 0), 4)
                     if o.get("n") and bo.get("n") else None)
        rows.append({
            "engine": name, "composite_oos": e["composite_oos"]["score"],
            "oos_n": o.get("n", 0), "oos_win": o.get("win_rate"),
            "oos_expectancy_R": o.get("expectancy_R"), "oos_pf": o.get("profit_factor"),
            "oos_maxDD_R": o.get("max_drawdown_R"), "oos_sharpe": o.get("sharpe"),
            "verdict": e["verdict"]["verdict"],
            "ann_status": e["ann"].get("status"),
            "ann_oos_expectancy_delta_R": ann_delta,
            "ann_oos_n_after": bo.get("n", 0),
        })
    rows.sort(key=lambda r: -r["composite_oos"])
    best = rows[0]["engine"] if rows else None
    hybrid = engines.get("E3_HYBRID", {})
    e1o = engines.get("E1_TREND", {}).get("A_engine_only", {}).get("oos", {})
    e2o = engines.get("E2_STRUCTURE", {}).get("A_engine_only", {}).get("oos", {})
    h_o = hybrid.get("A_engine_only", {}).get("oos", {})
    hybrid_beats_both = bool(
        h_o.get("n") and h_o.get("expectancy_R", -9) > max(e1o.get("expectancy_R", -9),
                                                           e2o.get("expectancy_R", -9))
        and h_o.get("profit_factor", 0) >= max(e1o.get("profit_factor", 0), e2o.get("profit_factor", 0)))
    ann_helps = any(r["ann_status"] == "OK" and (r["ann_oos_expectancy_delta_R"] or 0) > 0.02
                    and r["ann_oos_n_after"] >= 30 for r in rows)
    any_go = any(r["verdict"] == "GO" for r in rows)

    qual = _signal_qualification(engines, best) if any_go else None
    return {
        "ranking": rows,
        "best_standalone": best if any_go else None,
        "best_signal_quality": (max(rows, key=lambda r: (r["oos_pf"] or 0) * (r["oos_expectancy_R"] or 0))["engine"]
                                if any_go else None),
        "hybrid_outperforms_both": hybrid_beats_both,
        "ann_genuinely_helps_oos": ann_helps,
        "any_engine_go": any_go,
        "signal_qualification_rule": qual,
        "note": ("All three engines fail OOS -> NO-GO. Diagnosis in the per-engine "
                 "'failed' lists + by_regime / by_hour / by_side tables. No signal "
                 "is forced." if not any_go else
                 f"{best} ranks first on the OOS composite; see the qualification rule."),
    }


def _signal_qualification(engines: dict, best: str) -> dict:
    """Where does the winner's OOS expectancy concentrate? -> a qualification rule."""
    e = engines.get(best, {})
    reg = e.get("by_regime_oos", {})
    hour = e.get("by_hour_oos", {})
    side = e.get("by_side_oos", {})
    good_reg = [k for k, m in reg.items() if m.get("n", 0) >= 8 and m.get("expectancy_R", -9) > 0.05]
    good_hr = [k for k, m in hour.items() if m.get("n", 0) >= 8 and m.get("expectancy_R", -9) > 0.05]
    good_sd = [k for k, m in side.items() if m.get("n", 0) >= 8 and m.get("expectancy_R", -9) > 0.05]
    ann = e.get("ann", {})
    return {
        "engine": best,
        "conditions": {
            "regime_in": good_reg or "any (no strong concentration)",
            "hour_in": good_hr or "any",
            "side_in": good_sd or ["LONG", "SHORT"],
            "min_rr_room": 1.3,
            "min_entry_quality": 45,
            "ann_p_win_min": (round(ann.get("threshold", 0.5), 2)
                              if ann.get("status") == "OK" else "n/a (ANN not validated)"),
        },
        "note": "Qualify a research signal only when ALL conditions hold. Derived "
                "from OOS conditional performance, not tuned.",
    }


def _write(rep: dict, out: str) -> dict:
    os.makedirs(out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jp = os.path.join(out, f"tri_compare_{stamp}.json")
    mp = os.path.join(out, f"tri_compare_{stamp}.md")
    with open(jp, "w") as fh:
        json.dump(rep, fh, indent=2, default=str)
    with open(mp, "w") as fh:
        fh.write(_md(rep))
    return {"json": jp, "markdown": mp}


def _row(m: dict) -> str:
    if not m.get("n"):
        return "| 0 | | | | | | | | | | | |"
    return (f"| {m['n']} | {round(m['win_rate']*100,1)} | {m['avg_win_R']} | {m['avg_loss_R']} | "
            f"{m['expectancy_R']} | {m['profit_factor']} | {m['net_R']} | {m['max_drawdown_R']} | "
            f"{m['sharpe']} | {round(m['t1_rate']*100,1)}/{round(m['t2_rate']*100,1)}/{round(m['t3_rate']*100,1)} | "
            f"{round(m['sl_rate']*100,1)} | {m['avg_hold_bars']} |")


def _md(r: dict) -> str:
    L = [f"# TRI-COMPARE -- NIFTY {r['params']['tf_min']}m -- E1 Trend / E2 Structure(ChatGPT) / E3 Hybrid",
         "", f"_generated {r['generated_at']} · {r['params']['start']}..{r['params']['end']} · "
         f"runtime {r.get('runtime_seconds','?')}s_", ""]
    if r.get("status") != "OK":
        L.append(f"**STATUS: {r.get('status')}**")
        return "\n".join(L)
    cap = r["data_capability"]
    L += [f"data: {cap['source']} · {cap['n_sessions']} sessions · volume_available="
          f"{cap['volume_available']} (cash index -> volume features inert)",
          f"splits: TRAIN {r['splits']['train'][2]} · VAL {r['splits']['val'][2]} · OOS "
          f"{r['splits']['oos'][2]} sessions", ""]
    c = r["comparison"]
    L += ["## COMPARISON (OOS, engine-only)", "",
          "| rank | engine | composite | n | win% | expR | PF | maxDD R | Sharpe | verdict | ANN Δ expR |",
          "|--|--|--:|--:|--:|--:|--:|--:|--:|:--:|--:|"]
    for i, row in enumerate(c["ranking"], 1):
        L.append(f"| {i} | {row['engine']} | {row['composite_oos']} | {row['oos_n']} | "
                 f"{round((row['oos_win'] or 0)*100,1)} | {row['oos_expectancy_R']} | {row['oos_pf']} | "
                 f"{row['oos_maxDD_R']} | {row['oos_sharpe']} | **{row['verdict']}** | "
                 f"{row['ann_oos_expectancy_delta_R']} ({row['ann_status']}) |")
    L += ["", f"- best standalone: **{c['best_standalone']}**",
          f"- best signal quality: **{c['best_signal_quality']}**",
          f"- hybrid outperforms both E1 & E2 (OOS): **{c['hybrid_outperforms_both']}**",
          f"- ANN genuinely improves OOS: **{c['ann_genuinely_helps_oos']}**",
          f"- any engine GO: **{c['any_engine_go']}**", "", c["note"], ""]
    if c.get("signal_qualification_rule"):
        L += ["### Recommended signal-qualification rule", "```",
              json.dumps(c["signal_qualification_rule"], indent=2), "```", ""]

    for name, e in r["engines"].items():
        v = e["verdict"]
        L += ["", f"## {name} — verdict **{v['verdict']}**",
              f"signals {e['signals_total']} · ready {e['ready_total']} · entries {e['entries_total']} · "
              f"harness rejects {json.dumps(e['harness_rejects'])}",
              (f"failed: {v['failed']}" if v["failed"] else "clears all OOS gates"),
              f"ANN: {e['ann']}", "",
              "| slice | n | win% | avgW R | avgL R | expR | PF | net R | maxDD R | Sharpe | T1/T2/T3 % | SL% | hold |",
              "|--|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
        for k in ("all", "train", "val", "oos"):
            L.append(f"| {k} " + _row(e["A_engine_only"][k]))
        L.append(f"| oos+ANN " + _row(e["B_engine_plus_ann"]["oos"]))
        L += ["", "by regime (OOS): " + json.dumps({k: {"n": m.get("n"), "expR": m.get("expectancy_R"),
                                                        "PF": m.get("profit_factor")}
                                                    for k, m in e["by_regime_oos"].items()}),
              "by hour (OOS): " + json.dumps({k: {"n": m.get("n"), "expR": m.get("expectancy_R")}
                                              for k, m in e["by_hour_oos"].items()}),
              "by side (OOS): " + json.dumps({k: {"n": m.get("n"), "expR": m.get("expectancy_R"),
                                                  "PF": m.get("profit_factor")}
                                              for k, m in e["by_side_oos"].items()}),
              "by year: " + json.dumps({k: {"n": m.get("n"), "netR": m.get("net_R")}
                                        for k, m in e["by_year"].items()}),
              "walk-forward A vs B (expR): " + json.dumps(
                  [{"fold": f["fold"], "test": f["test"], "A": f["A"].get("expectancy_R"),
                    "A_n": f["A"].get("n"), "B": f["B"].get("expectancy_R"), "B_n": f["B"].get("n")}
                   for f in e["walk_forward"]]),
              f"composite OOS: {e['composite_oos']}"]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="TRI-COMPARE 3-engine NIFTY backtest (research only)")
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--out", default="data/research/tri_compare")
    a = ap.parse_args(argv)
    rep = run(tf_min=a.tf, start=a.start, end=a.end, out=a.out)
    print("STATUS:", rep.get("status"))
    if rep.get("status") == "OK":
        for row in rep["comparison"]["ranking"]:
            print(f"  {row['engine']:13s} composite={row['composite_oos']:>5} "
                  f"OOS n={row['oos_n']:4d} expR={row['oos_expectancy_R']} PF={row['oos_pf']} "
                  f"{row['verdict']}  (ANN Δ {row['ann_oos_expectancy_delta_R']}/{row['ann_status']})")
        c = rep["comparison"]
        print(f"  best_standalone={c['best_standalone']}  hybrid_beats_both={c['hybrid_outperforms_both']}  "
              f"ann_helps={c['ann_genuinely_helps_oos']}  any_go={c['any_engine_go']}")
    print("files:", rep.get("_files"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
