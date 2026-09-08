"""
Orchestrator + reproducible CLI for the Multi-Candle Order-Pressure engine.

    python -m app.research_engines.order_pressure.backtest \
        --symbol NIFTY --tfs 1m,3m,5m \
        --start 2019-01-01 --end 2025-12-24 \
        --out data/research/order_pressure

RESEARCH ONLY. Reads Kaggle / market_history.db, writes JSON + Markdown under
--out. No broker, no live wiring, no changes to application logic.

Produces the 17-item report and a GO / NO-GO verdict driven ONLY by the
out-of-sample (purged walk-forward) evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from . import data as D
from . import features as FE
from . import labels as LB
from . import models as M
from . import walkforward as WF
from . import trade_mgmt as TM
from .config import merged
from .metrics import classification, trading
from .formulas import atr


# --------------------------------------------------------------------------- regime
def _regimes(bars, cfg):
    out = []
    W = 20
    for i in range(len(bars)):
        if i < W:
            out.append("WARMUP"); continue
        seg = bars[i - W:i + 1]
        a = atr(seg, cfg["atr_window"], cfg) or cfg["eps"]
        slope = (seg[-1]["c"] - seg[0]["c"]) / (W * a)
        rng = (max(x["h"] for x in seg) - min(x["l"] for x in seg)) / a
        if slope > 0.15:
            out.append("TREND_UP")
        elif slope < -0.15:
            out.append("TREND_DOWN")
        else:
            out.append("RANGE" if rng < 6.0 else "CHOP")
    return out


# ------------------------------------------------------------------- dataset
def _dataset(symbol, tf, start, end, cfg, mtf_bars=None):
    bars = D.load_spot_bars(symbol, tf, start=start, end=end)
    if len(bars) < cfg["min_rows_for_fit"] * 2:
        return None
    pr = FE.precompute_pressure(bars, cfg)
    regs = _regimes(bars, cfg)
    names = FE.feature_names(cfg)
    core = cfg["spot_core_features"]
    # multi-TF: coarse net-pressure by timestamp -> nearest lookup
    def coarse_net(tag):
        cb = mtf_bars.get(tag) if mtf_bars else None
        if not cb:
            return None
        cp = FE.precompute_pressure(cb, cfg)
        return [(cb[k]["t"], cp[k]["net"]) for k in range(len(cb))]
    n3 = coarse_net("3m")
    n5 = coarse_net("5m")

    def nearest(series, t):
        if not series:
            return 0.0
        lo, hi = 0, len(series) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if series[mid][0] < t:
                lo = mid + 1
            else:
                hi = mid
        return series[max(0, lo - 1)][1]

    X_full, X_core, y, dts, reg_used = [], [], [], [], []
    for i in range(max(20, cfg["atr_window"] + 2), len(bars) - 1):
        lab = LB.next_candle_label(bars, i, cfg)
        if lab is None or regs[i] == "WARMUP":
            continue
        mtf = FE.mtf_fusion(pr[i]["net"], nearest(n3, bars[i]["t"]) if tf != "3m" else pr[i]["net"],
                            nearest(n5, bars[i]["t"]) if tf != "5m" else pr[i]["net"], cfg)
        row = FE.build_row(bars, pr, i, cfg, mtf=mtf)
        X_full.append([row[n] for n in names])
        X_core.append([row[n] for n in core])
        y.append(lab); dts.append(row_session(bars[i])); reg_used.append(regs[i])
    return {"bars": bars, "pr": pr, "X_full": X_full, "X_core": X_core, "y": y,
            "dates": dts, "regimes": reg_used, "feat_names": names, "core_names": core}


def row_session(bar):
    return bar.get("session_date") or datetime.fromtimestamp(
        bar["t"], tz=timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------------- model sweep
def _sweep(ds, cfg):
    core_idx = {n: k for k, n in enumerate(ds["core_names"])}
    factories = {
        "majority": (M.MajorityBaseline, "core"),
        "persistence": (lambda: M.PersistenceBaseline(core_idx), "core"),
        "net_pressure_logit": (lambda: M.NetPressureLogit(core_idx), "core"),
        "multinomial_logit": (M.MultinomialLogit, "core"),
        "gb_stumps": (M.GBStumps, "full"),
        "mlp": (M.MLP, "core"),
    }
    res = {}
    for name, (fac, which) in factories.items():
        X = ds["X_core"] if which == "core" else ds["X_full"]
        t = time.time()
        r = WF.run(X, ds["y"], ds["dates"], fac, cfg=cfg, calibrate=(name not in ("majority",)))
        r["fit_seconds"] = round(time.time() - t, 1)
        r["feature_set"] = which
        res[name] = r
    return res


def _per_regime(ds, cfg, best_name):
    """Re-run the winning model, but score OOS pairs bucketed by regime."""
    core_idx = {n: k for k, n in enumerate(ds["core_names"])}
    fac = {"multinomial_logit": M.MultinomialLogit, "gb_stumps": M.GBStumps, "mlp": M.MLP,
           "net_pressure_logit": lambda: M.NetPressureLogit(core_idx),
           "persistence": lambda: M.PersistenceBaseline(core_idx),
           "majority": M.MajorityBaseline}[best_name]
    which = "full" if best_name == "gb_stumps" else "core"
    X = ds["X_full"] if which == "full" else ds["X_core"]
    # single expanding split at 70% for a regime read (cheap)
    n = len(X); cut = int(n * 0.7); vcut = int(n * 0.85)
    m = fac()
    m.fit(X[:vcut], ds["y"][:vcut], X_val=X[vcut:cut] or X[cut - 50:cut],
          y_val=ds["y"][vcut:cut] or ds["y"][cut - 50:cut])
    by = {}
    for i in range(cut, n):
        p = m.predict_proba(X[i]); reg = ds["regimes"][i]
        by.setdefault(reg, []).append((p, ds["y"][i]))
    return {reg: classification(pairs) for reg, pairs in by.items() if len(pairs) >= 30}


# ------------------------------------------------------- trade management eval
def _tm_eval(ds, cfg, best_name, best_wf):
    """Entries from the winning model's confident OOS calls (last 30% span),
    TM heads fit on the first 60% of those entries, evaluated on the rest."""
    bars, pr = ds["bars"], ds["pr"]
    core_idx = {n: k for k, n in enumerate(ds["core_names"])}
    fac = {"multinomial_logit": M.MultinomialLogit, "gb_stumps": M.GBStumps, "mlp": M.MLP}.get(best_name)
    if fac is None:
        return {"status": "SKIPPED", "reason": f"winning model {best_name} is a baseline"}
    which = "full" if best_name == "gb_stumps" else "core"
    X = ds["X_full"] if which == "full" else ds["X_core"]
    n = len(X); cut = int(n * 0.6); vcut = int(n * 0.75)
    m = fac(); m.fit(X[:vcut], ds["y"][:vcut], X_val=X[vcut:cut] or X[cut - 50:cut],
                     y_val=ds["y"][vcut:cut] or ds["y"][cut - 50:cut])
    # map dataset row index -> bar index (rows start at first labelled bar)
    first_bar = max(20, cfg["atr_window"] + 2)
    entries = []
    for r in range(cut, n):
        p = m.predict_proba(X[r])
        top = max(p, key=p.get)
        if top in ("UP", "DOWN") and p[top] >= 0.45:
            entries.append((first_bar + r, "LONG" if top == "UP" else "SHORT"))
    if len(entries) < 40:
        return {"status": "INSUFFICIENT_SAMPLE", "n_entries": len(entries)}
    split = int(len(entries) * 0.6)
    tm_models = TM.fit_tm_models(bars, pr, entries[:split], cfg)
    ev_entries = entries[split:]
    pol = TM.run_policies(bars, pr, ev_entries, cfg, tm_models)
    metr = {k: trading(v) for k, v in pol.items()}
    # early-exit improvement + SL avoidance / recovery
    fx, ee = pol["fixed_sl"], pol["early_exit"]
    fx_by_i = {t["entry_i"]: t for t in fx}
    avoided = recov_ok = 0
    for t in ee:
        f = fx_by_i.get(t["entry_i"])
        if f and f["exit_reason"] == "STOP" and t["exit_reason"] != "STOP":
            avoided += 1
            if t["points"] > f["points"]:
                recov_ok += 1
    metr["early_exit_improvement_R"] = round(
        (metr["early_exit"].get("expectancy_R", 0) or 0) - (metr["fixed_sl"].get("expectancy_R", 0) or 0), 4)
    metr["sl_avoidance"] = {
        "fixed_sl_stops": sum(1 for t in fx if t["exit_reason"] == "STOP"),
        "stops_avoided_by_early_exit": avoided,
        "avoided_and_better": recov_ok,
        "recovered_before_sl_rate_fixed": round(
            sum(1 for t in fx if t["recovered_before_sl"]) / max(1, len(fx)), 3),
    }
    metr["status"] = "OK"
    metr["n_eval_entries"] = len(ev_entries)
    return metr


# --------------------------------------------------------------- option demo
def _option_demo(cfg):
    out = {}
    for sym in ("CRUDEOIL", "NATURALGAS"):
        rows = {"CE": D.load_option_bars(sym, "CE", "5m"), "PE": D.load_option_bars(sym, "PE", "5m")}
        blk = {}
        for side, bars in rows.items():
            bars = [b for b in bars if b.get("n", 0) >= 2]
            if len(bars) < 20:
                blk[side] = {"status": "INSUFFICIENT", "bars": len(bars)}
                continue
            pr = FE.precompute_pressure(bars, cfg)
            states = []
            for i in range(1, len(bars)):
                a = atr(bars[max(0, i - 14):i + 1], 14, cfg) or cfg["eps"]
                st = FE.oi_price_state(bars[i]["c"] - bars[i - 1]["c"], bars[i].get("oi"),
                                      bars[i].get("oi_change"), a, cfg)
                states.append(st["state"])
            blk[side] = {
                "status": "USABLE_DEMO", "bars": len(bars),
                "sessions": len(set(b["session_date"] for b in bars)),
                "mean_net_pressure": round(sum(p["net"] for p in pr) / len(pr), 2),
                "oi_state_mix": {s: round(states.count(s) / len(states), 3)
                                 for s in set(states)},
                "note": "descriptive only -- sample far below any OOS threshold",
            }
        out[sym] = blk
    return out


# --------------------------------------------------------------- GO / NO-GO
def _verdict(report):
    reasons = []
    tf_pass = 0
    for tf, blk in report["by_timeframe"].items():
        sw = blk["model_sweep"]
        cand = {k: sw[k] for k in ("multinomial_logit", "gb_stumps", "mlp")
                if sw[k].get("status") == "OK"}
        base = {k: sw[k] for k in ("majority", "persistence", "net_pressure_logit")
                if sw[k].get("status") == "OK"}
        if not cand or not base:
            reasons.append(f"{tf}: walk-forward INSUFFICIENT_SAMPLE")
            continue
        bm = min(cand.values(), key=lambda r: r["log_loss"])
        bb = min(base.values(), key=lambda r: r["log_loss"])
        blk["best_model_oos"] = {"name": [k for k, v in cand.items() if v is bm][0], **{
            k: bm[k] for k in ("accuracy", "macro_f1", "log_loss", "brier", "ece", "n")}}
        blk["best_baseline_oos"] = {"name": [k for k, v in base.items() if v is bb][0], **{
            k: bb[k] for k in ("accuracy", "macro_f1", "log_loss", "n")}}
        beats = (bm["macro_f1"] >= bb["macro_f1"] + 0.02 and bm["log_loss"] <= bb["log_loss"] - 0.01)
        calib_ok = bm["ece"] <= 0.06
        if beats and calib_ok:
            tf_pass += 1
        else:
            if not beats:
                reasons.append(f"{tf}: best model ({blk['best_model_oos']['name']}) macro_f1 "
                               f"{bm['macro_f1']} / logloss {bm['log_loss']} did NOT beat baseline "
                               f"({blk['best_baseline_oos']['name']}) {bb['macro_f1']} / {bb['log_loss']}")
            if not calib_ok:
                reasons.append(f"{tf}: OOS ECE {bm['ece']} > 0.06 (mis-calibrated)")
    # regime robustness (on the tf with the best model, if any passed)
    regime_ok = False
    for tf, blk in report["by_timeframe"].items():
        pr = blk.get("per_regime") or {}
        good = [r for r, mm in pr.items() if mm["macro_f1"] >= mm["base_rate_accuracy"] + 0.02]
        if len(good) >= 2:
            regime_ok = True
        elif pr:
            reasons.append(f"{tf}: model beats base-rate in only {len(good)} regime(s) (<2)")
    # trade management
    tm_ok = False
    for tf, blk in report["by_timeframe"].items():
        tm = blk.get("trade_management") or {}
        if tm.get("status") == "OK":
            best_pol = max(("fixed_sl", "early_exit", "trailing_sl"),
                           key=lambda p: tm[p].get("expectancy_R", -9))
            e = tm[best_pol].get("expectancy_R", -9)
            pf = tm[best_pol].get("profit_factor", 0)
            if e > 0 and pf >= 1.2:
                tm_ok = True
            else:
                reasons.append(f"{tf}: best exit policy ({best_pol}) OOS expectancy {e}R, PF {pf} "
                               "-- not positive-with-margin")

    go = tf_pass >= 2 and regime_ok and tm_ok
    return {
        "verdict": "GO" if go else "NO-GO",
        "timeframes_passing_signal_gate": tf_pass,
        "regime_robustness_ok": regime_ok,
        "trade_management_ok": tm_ok,
        "failed_components": reasons if not go else [],
        "note": ("All OOS gates cleared." if go else
                 "Do NOT wire into production. Failing components listed above. "
                 "This matches the prior audits: on this problem a small NN does not "
                 "beat simple baselines out-of-sample."),
    }


# --------------------------------------------------------------------- run
def run(symbol="NIFTY", tfs=("1m", "3m", "5m"), start="2019-01-01", end=None,
        out="data/research/order_pressure", config=None) -> dict:
    cfg = merged(config)
    t0 = time.time()
    report = {
        "engine": "multi_candle_order_pressure",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {"symbol": symbol, "tfs": list(tfs), "start": start, "end": end,
                   "config": cfg},
        "coverage": D.coverage_report(),
        "by_timeframe": {},
        "option_demonstrator": _option_demo(cfg),
    }
    # preload coarse bars once for MTF
    mtf_bars = {tag: D.load_spot_bars(symbol, tag, start=start, end=end) for tag in ("3m", "5m")}
    for tf in tfs:
        ds = _dataset(symbol, tf, start, end, cfg, mtf_bars=mtf_bars)
        if ds is None:
            report["by_timeframe"][tf] = {"status": "INSUFFICIENT_SAMPLE"}
            continue
        sweep = _sweep(ds, cfg)
        blk = {
            "status": "OK",
            "n_rows": len(ds["y"]),
            "label_distribution": LB.label_distribution(ds["y"]),
            "sessions": len(set(ds["dates"])),
            "feature_count": {"full": len(ds["feat_names"]), "core": len(ds["core_names"])},
            "model_sweep": {k: {kk: v.get(kk) for kk in
                                ("status", "accuracy", "macro_f1", "log_loss", "brier", "ece",
                                 "n", "n_folds", "fit_seconds", "feature_set", "reason")}
                            for k, v in sweep.items()},
            "confusion_best": None,
        }
        ok = {k: v for k, v in sweep.items() if v.get("status") == "OK"
              and k in ("multinomial_logit", "gb_stumps", "mlp")}
        if ok:
            best_name = min(ok, key=lambda k: ok[k]["log_loss"])
            blk["confusion_best"] = sweep[best_name].get("confusion")
            blk["reliability_best"] = sweep[best_name].get("reliability")
            blk["per_regime"] = _per_regime(ds, cfg, best_name)
            blk["trade_management"] = _tm_eval(ds, cfg, best_name, sweep[best_name])
        report["by_timeframe"][tf] = blk

    report["go_no_go"] = _verdict(report)
    report["runtime_seconds"] = round(time.time() - t0, 1)

    os.makedirs(out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jp = os.path.join(out, f"order_pressure_backtest_{symbol}_{stamp}.json")
    with open(jp, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    mp = os.path.join(out, f"order_pressure_backtest_{symbol}_{stamp}.md")
    with open(mp, "w") as fh:
        fh.write(_markdown(report))
    report["_files"] = {"json": jp, "markdown": mp}
    return report


def _markdown(r) -> str:
    L = ["# Multi-Candle Order-Pressure Engine — backtest", "",
         f"_generated {r['generated_at']} · symbol {r['params']['symbol']} · "
         f"tfs {','.join(r['params']['tfs'])} · {r['params']['start']}..{r['params']['end']} · "
         f"runtime {r['runtime_seconds']}s_", "",
         "## VERDICT: **" + r["go_no_go"]["verdict"] + "**", "",
         r["go_no_go"]["note"], ""]
    if r["go_no_go"]["failed_components"]:
        L.append("Failed components:")
        L += [f"- {x}" for x in r["go_no_go"]["failed_components"]]
        L.append("")
    L += ["## Data coverage", "", "```", json.dumps(r["coverage"]["summary"]), "```",
          "Unavailable (reported, not invented):"]
    L += [f"- {x}" for x in r["coverage"]["unavailable"]]
    for tf, blk in r["by_timeframe"].items():
        L += ["", f"## Timeframe {tf}", ""]
        if blk.get("status") != "OK":
            L.append(f"`{blk.get('status')}`"); continue
        L.append(f"rows {blk['n_rows']} · sessions {blk['sessions']} · "
                 f"labels {blk['label_distribution']}")
        L += ["", "| model | set | folds | acc | macroF1 | logloss | brier | ece | fit s |",
              "|---|---|--:|--:|--:|--:|--:|--:|--:|"]
        for k, m in blk["model_sweep"].items():
            L.append(f"| {k} | {m.get('feature_set','-')} | {m.get('n_folds','-')} | "
                     f"{m.get('accuracy','-')} | {m.get('macro_f1','-')} | {m.get('log_loss','-')} | "
                     f"{m.get('brier','-')} | {m.get('ece','-')} | {m.get('fit_seconds','-')} |")
        if blk.get("best_model_oos"):
            L += ["", f"**OOS best model:** {blk['best_model_oos']}",
                  f"**OOS best baseline:** {blk['best_baseline_oos']}"]
        if blk.get("per_regime"):
            L += ["", "### per regime (single 70/30 split)", "",
                  "| regime | n | acc | macroF1 | base-rate acc |", "|---|--:|--:|--:|--:|"]
            for reg, mm in blk["per_regime"].items():
                L.append(f"| {reg} | {mm['n']} | {mm['accuracy']} | {mm['macro_f1']} | "
                         f"{mm['base_rate_accuracy']} |")
        tm = blk.get("trade_management") or {}
        if tm.get("status") == "OK":
            L += ["", "### dynamic trade management (OOS entries from the winning model)", "",
                  f"eval entries: {tm['n_eval_entries']}", "",
                  "| policy | n | win% | exp R | PF | maxDD R | MAE R | MFE R |",
                  "|---|--:|--:|--:|--:|--:|--:|--:|"]
            for pol in ("fixed_sl", "early_exit", "trailing_sl"):
                m = tm[pol]
                L.append(f"| {pol} | {m.get('n')} | {m.get('win_rate')} | {m.get('expectancy_R')} | "
                         f"{m.get('profit_factor')} | {m.get('max_drawdown_R')} | "
                         f"{m.get('mae_R_mean')} | {m.get('mfe_R_mean')} |")
            L += ["", f"early-exit improvement: {tm['early_exit_improvement_R']} R/trade",
                  f"SL avoidance: {json.dumps(tm['sl_avoidance'])}"]
        elif tm:
            L.append(f"trade management: `{tm.get('status')}` {tm.get('reason','')}")
    L += ["", "## Option demonstrator (CRUDEOIL / NATURALGAS, 5m poll snapshots)", "",
          "```", json.dumps(r["option_demonstrator"], indent=2), "```",
          "*Option-side sample is 5–7 poll sessions per symbol → descriptive only, "
          "no predictive / OOS claim possible.*", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Multi-Candle Order-Pressure backtest (research only)")
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--tfs", default="1m,3m,5m")
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", default="data/research/order_pressure")
    ap.add_argument("--coverage-only", action="store_true")
    a = ap.parse_args(argv)
    if a.coverage_only:
        print(json.dumps(D.coverage_report(), indent=2, default=str)); return 0
    rep = run(symbol=a.symbol, tfs=tuple(a.tfs.split(",")), start=a.start, end=a.end, out=a.out)
    print("\n==== VERDICT:", rep["go_no_go"]["verdict"], "====")
    for x in rep["go_no_go"]["failed_components"]:
        print("  -", x)
    print("files:", rep["_files"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
