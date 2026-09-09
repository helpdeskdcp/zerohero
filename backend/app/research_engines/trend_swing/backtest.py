"""
Daily trend-swing backtest for the E1 hypothesis.

    python -m app.research_engines.trend_swing.backtest \
        --start 2010-01-01 --end 2025-12-31 --out data/research/trend_swing

Runs the RAW frozen E1 engine on daily NIFTY, combined + long + short, with a
chronological TRAIN -> VAL -> OOS split and a PURGED expanding walk-forward.
Compares to buy-and-hold + a simple EMA-cross + a simple Donchian baseline.
Crisis-window diagnostic. Verdict GO / NO-GO / INCONCLUSIVE on the RAW engine.
ANN is reported AFTER, information-only -- it cannot change a NO-GO.

RESEARCH ONLY. No broker, no order path, live_trading untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from . import data as D
from . import engine as E
from . import harness as HN
from . import benchmark as BM
from . import metrics as MT
from .config import merged


def _purge(trades: list[dict], boundary: str, embargo_days: int, all_dates: list[str]):
    """Drop trades straddling `boundary`; also drop trades that OPEN within
    `embargo_days` trading sessions after it."""
    try:
        bi = all_dates.index(boundary)
    except ValueError:
        bi = 0
    embargo = set(all_dates[bi: bi + embargo_days])
    kept = []
    for t in trades:
        if t["entry_date"] < boundary <= t["exit_date"]:
            continue
        if t["entry_date"] in embargo:
            continue
        kept.append(t)
    return kept


def _slice(trades, lo, hi):
    return [t for t in trades if lo <= t["entry_date"] < hi]


def _slice_daily(daily, lo, hi):
    return [d for d in daily if lo <= d["date"] < hi]


def _walk_forward(trades, daily, dates, cfg):
    if len(dates) < cfg["wf_folds"] + 2:
        return []
    fold = len(dates) // (cfg["wf_folds"] + 1)
    out = []
    for k in range(1, cfg["wf_folds"] + 1):
        b0 = dates[fold * k]
        b1 = dates[min(fold * (k + 1), len(dates) - 1)]
        te = _purge(_slice(trades, b0, b1), b0, cfg["purge_days"], dates)
        te = _purge(te, b1, cfg["purge_days"], dates)
        dte = _slice_daily(daily, b0, b1)
        out.append({"fold": k, "test": [b0, b1],
                    "trades": MT.trade_metrics(te), "daily": MT.daily_metrics(dte)})
    return out


def _verdict(all_t, oos_t, oos_d, tr_d, yr_tbl, wf, bh_oos_sharpe, crisis, cfg) -> dict:
    """Evidence-based. A daily single-index trend system is INHERENTLY
    low-frequency (~4 trades/yr) so the trade-count floor rarely fits a 4-yr
    OOS -- we judge on the abundant daily-return series + every walk-forward
    fold + the baseline comparison + crisis alpha, and use trade counts as a
    tie-breaker, not a gate."""
    n_oos = oos_t.get("n", 0)
    n_all = all_t.get("n", 0)
    exp_oos = oos_t.get("expectancy_R", -9)
    exp_all = all_t.get("expectancy_R", -9)
    sh_oos = oos_d.get("sharpe")
    dd = abs(oos_d.get("equity_maxdd_R", 999))
    pos_y = MT.positive_years(yr_tbl)
    pos_wf = sum(1 for f in wf if f["trades"].get("n", 0) >= 4 and f["trades"].get("expectancy_R", -9) > 0)
    tr_sh = tr_d.get("sharpe")
    degr = (abs(tr_sh - sh_oos) / abs(tr_sh)) if (tr_sh and sh_oos) else 1.0
    beats_bh = (sh_oos is not None and bh_oos_sharpe is not None and sh_oos > bh_oos_sharpe)
    crisis_pos = crisis.get("crisis_alpha_positive", False)

    why = []
    # ---- NO-GO: enough evidence to reject, regardless of the OOS trade count ----
    hard = []
    if n_all >= 40 and exp_all <= 0:
        hard.append(f"all-period expectancy {exp_all} <= 0 over {n_all} trades")
    if sh_oos is not None and sh_oos < 0:
        hard.append(f"OOS daily Sharpe {sh_oos} < 0")
    if pos_wf <= 1 and len(wf) >= 5:
        hard.append(f"only {pos_wf}/{len(wf)} walk-forward folds positive")
    if exp_oos <= 0 and n_oos >= 8:
        hard.append(f"OOS expectancy {exp_oos} <= 0")
    if not crisis_pos:
        hard.append("crisis-alpha NEGATIVE — the core TSMOM rationale fails here")
    if sh_oos is not None and bh_oos_sharpe is not None and sh_oos < bh_oos_sharpe - 0.3:
        hard.append(f"loses to buy&hold OOS (Sharpe {sh_oos} vs {round(bh_oos_sharpe,2)})")
    if len(hard) >= 2:
        return {"verdict": "NO-GO", "why": hard, "positive_years": pos_y,
                "positive_wf_folds": f"{pos_wf}/{len(wf)}", "degradation": round(degr, 3),
                "beats_buy_and_hold_oos": beats_bh}

    full_pass = (exp_oos > cfg["verdict_min_expectancy_R"] and exp_all > 0
                 and sh_oos is not None and sh_oos >= cfg["verdict_min_sharpe"]
                 and oos_t.get("profit_factor", 0) >= cfg["verdict_min_pf"]
                 and len(pos_y) >= cfg["verdict_min_pos_years"]
                 and pos_wf >= cfg["verdict_min_pos_wf_folds"]
                 and degr <= cfg["verdict_max_degradation"]
                 and beats_bh and crisis_pos and dd <= cfg["verdict_max_dd_R"])
    if full_pass:
        return {"verdict": "GO", "positive_years": pos_y, "positive_wf_folds": f"{pos_wf}/{len(wf)}",
                "degradation": round(degr, 3), "beats_buy_and_hold_oos": True,
                "why": [f"OOS expR {exp_oos}, PF {oos_t['profit_factor']}, Sharpe {sh_oos}, "
                        f"+years {len(pos_y)}, +WF {pos_wf}/{len(wf)}, beats B&H, crisis-alpha +"]}
    # ---- otherwise INCONCLUSIVE ----
    why = [f"OOS trades {n_oos} (< {cfg['min_trades_for_verdict']}) — daily single-index is "
           f"low-frequency; judged on daily series + WF instead"]
    for label, bad in (
        (f"OOS Sharpe {sh_oos} < {cfg['verdict_min_sharpe']}", (sh_oos or -9) < cfg["verdict_min_sharpe"]),
        (f"all-period expectancy {exp_all} <= 0", exp_all <= 0),
        (f"positive WF folds {pos_wf}/{len(wf)} < {cfg['verdict_min_pos_wf_folds']}",
         pos_wf < cfg["verdict_min_pos_wf_folds"]),
        (f"does not beat buy&hold OOS", not beats_bh),
        ("crisis-alpha not positive", not crisis_pos)):
        if bad:
            why.append(label)
    return {"verdict": "INCONCLUSIVE", "why": why, "positive_years": pos_y,
            "positive_wf_folds": f"{pos_wf}/{len(wf)}", "degradation": round(degr, 3),
            "beats_buy_and_hold_oos": beats_bh}


def _side_block(bars, cfg, dates, split, side_filter, frame, tgt, *,
                bh_oos_sharpe=None, crisis=None):
    lo_tr, cut1, cut2 = split
    sim = HN.simulate(bars, cfg, side_filter=side_filter, frame=frame, tgt=tgt)
    tr, daily = sim["trades"], sim["daily_R"]
    tr_tr = _purge(_slice(tr, dates[0], cut1), cut1, cfg["purge_days"], dates)
    tr_va = _purge(_purge(_slice(tr, cut1, cut2), cut1, cfg["purge_days"], dates), cut2, cfg["purge_days"], dates)
    tr_oo = _purge(_slice(tr, cut2, dates[-1] + "z"), cut2, cfg["purge_days"], dates)
    d_tr = _slice_daily(daily, dates[0], cut1)
    d_va = _slice_daily(daily, cut1, cut2)
    d_oo = _slice_daily(daily, cut2, dates[-1] + "z")
    yr = MT.by_year(tr)
    wf = _walk_forward(tr, daily, dates, cfg)
    block = {
        "all": {"trades": MT.trade_metrics(tr), "daily": MT.daily_metrics(daily)},
        "train": {"trades": MT.trade_metrics(tr_tr), "daily": MT.daily_metrics(d_tr)},
        "val": {"trades": MT.trade_metrics(tr_va), "daily": MT.daily_metrics(d_va)},
        "oos": {"trades": MT.trade_metrics(tr_oo), "daily": MT.daily_metrics(d_oo)},
        "by_year": yr, "walk_forward": wf, "_trades": tr, "_daily": daily,
    }
    if side_filter is None:
        block["by_side"] = MT.by_side(tr)
        block["verdict"] = _verdict(block["all"]["trades"], block["oos"]["trades"],
                                    block["oos"]["daily"], block["train"]["daily"],
                                    yr, wf, bh_oos_sharpe, crisis or {}, cfg)
    return block


def run(start="2010-01-01", end="2025-12-31", out="data/research/trend_swing",
        config=None) -> dict:
    cfg = merged({**(config or {}), "start": start, "end": end})
    t0 = time.time()
    bars, cap = D.load_daily(cfg["symbol"], start=start, end=end)
    rep = {"engine": "trend_swing_E1_daily", "generated_at": datetime.now(timezone.utc).isoformat(),
           "params": {"symbol": cfg["symbol"], "start": start, "end": end}, "capability": cap,
           "config": cfg}
    if cap["n_days"] < 800:
        rep["status"] = "INSUFFICIENT_SAMPLE"
        _write(rep, out)
        return rep

    frame = E.build(bars, cfg)
    tgt = E.target_direction(frame, cfg)
    dates = [b["date"] for b in bars]
    a = int(len(dates) * cfg["train_frac"])
    b = int(len(dates) * (cfg["train_frac"] + cfg["val_frac"]))
    cut1, cut2 = dates[a], dates[b]
    split = (dates[0], cut1, cut2)
    rep["splits"] = {"train": [dates[0], cut1], "val": [cut1, cut2], "oos": [cut2, dates[-1]],
                     "n_days": len(dates)}

    # ---- baselines FIRST (needed by the verdict) ----
    bench = BM.run(bars, cfg)
    rep["baselines"] = {}
    for k, v in bench.items():
        rep["baselines"][k] = {
            "all": {"trades": MT.trade_metrics(v["trades"]), "daily": MT.daily_metrics(v["daily_R"])},
            "oos": {"trades": MT.trade_metrics(_slice(v["trades"], cut2, dates[-1] + "z")),
                    "daily": MT.daily_metrics(_slice_daily(v["daily_R"], cut2, dates[-1] + "z"))},
        }
    bh_oos_sharpe = rep["baselines"]["buy_and_hold"]["oos"]["daily"].get("sharpe")

    # ---- crisis diagnostic (combined engine, whole period) ----
    windows = MT.crisis_windows(bars, cfg)
    comb_sim = HN.simulate(bars, cfg, frame=frame, tgt=tgt)
    rep["crisis"] = MT.crisis_performance(comb_sim["daily_R"], bars, windows)

    rep["combined"] = _side_block(bars, cfg, dates, split, None, frame, tgt,
                                  bh_oos_sharpe=bh_oos_sharpe, crisis=rep["crisis"])
    rep["long_only"] = _side_block(bars, cfg, dates, split, "LONG", frame, tgt)
    rep["short_only"] = _side_block(bars, cfg, dates, split, "SHORT", frame, tgt)
    rep["crisis"]["buy_and_hold_R_during_crises"] = round(
        sum(d["r"] for d in bench["buy_and_hold"]["daily_R"]
            if d["date"] in {bars[k]["date"] for w in windows for k in range(w["i0"], w["i1"] + 1)}), 2)

    # ---- ANN (information only; after the raw baseline; cannot rescue a NO-GO) ----
    rep["ann_note"] = ("ANN not run in this baseline pass. The verdict above is on the RAW "
                       "frozen E1 engine. Per the brief, ANN may be tested afterwards but "
                       "must not turn a NO-GO into a GO.")

    for blk in ("combined", "long_only", "short_only"):
        rep[blk].pop("_trades", None)
        rep[blk].pop("_daily", None)
    rep["status"] = "OK"
    rep["runtime_seconds"] = round(time.time() - t0, 1)
    rep["_files"] = _write(rep, out)
    return rep


def _write(rep, out):
    os.makedirs(out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jp = os.path.join(out, f"trend_swing_{stamp}.json")
    mp = os.path.join(out, f"trend_swing_{stamp}.md")
    with open(jp, "w") as fh:
        json.dump(rep, fh, indent=2, default=str)
    with open(mp, "w") as fh:
        fh.write(_md(rep))
    return {"json": jp, "markdown": mp}


def _tm(m):
    t, d = m["trades"], m["daily"]
    if not t.get("n"):
        return "| 0 | | | | | | | | | | |"
    return (f"| {t['n']} | {round(t['win_rate']*100,1)} | {t['expectancy_R']} | {t['profit_factor']} | "
            f"{t['net_R']} | {t['max_drawdown_R']} | {t['max_consec_losses']} | {round(t['sl_rate']*100,1)} | "
            f"{t['avg_hold_days']} | {d.get('sharpe')} | {d.get('exposure')} |")


def _md(r):
    L = [f"# TREND-SWING (daily) -- E1 hypothesis -- {r['params']['symbol']}", "",
         f"_generated {r['generated_at']} · {r['params']['start']}..{r['params']['end']} · "
         f"runtime {r.get('runtime_seconds','?')}s_", ""]
    if r.get("status") != "OK":
        L.append(f"**STATUS: {r.get('status')}**")
        return "\n".join(L)
    cap = r["capability"]
    L += [f"data: {cap['source']} · {cap['n_days']} daily bars · {cap['range'][0]}..{cap['range'][1]}",
          f"splits: TRAIN {r['splits']['train'][0]}..{r['splits']['train'][1]} · "
          f"VAL ..{r['splits']['val'][1]} · OOS ..{r['splits']['oos'][1]}", ""]
    v = r["combined"]["verdict"]
    L += [f"## VERDICT (raw combined engine): **{v['verdict']}**", ""] + [f"- {w}" for w in v["why"]] + [""]

    hdr = ("| slice | n | win% | expR | PF | net R | maxDD R | maxConsecL | SL% | hold d | Sharpe | expo |",
           "|--|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    for blk, title in (("combined", "COMBINED (long+short)"), ("long_only", "LONG only"),
                       ("short_only", "SHORT only")):
        L += ["", f"## {title}", "", *hdr]
        for k in ("all", "train", "val", "oos"):
            L.append(f"| {k} " + _tm(r[blk][k]))
        wf = r[blk]["walk_forward"]
        L += ["", "walk-forward (purged, expanding): " + json.dumps(
            [{"fold": f["fold"], "test": f["test"], "n": f["trades"].get("n"),
              "expR": f["trades"].get("expectancy_R"), "netR": f["trades"].get("net_R"),
              "PF": f["trades"].get("profit_factor"), "sharpe": f["daily"].get("sharpe")}
             for f in wf]),
            "by year (net R): " + json.dumps({y: m.get("net_R") for y, m in r[blk]["by_year"].items()
                                              if m.get("n")})]
    L += ["", "## Baselines (OOS)", "",
          "| strategy | n | expR | PF | net R | Sharpe | maxDD R | total R (all) |",
          "|--|--:|--:|--:|--:|--:|--:|--:|"]
    for k, bb in r["baselines"].items():
        o, al = bb["oos"], bb["all"]
        ot, od = o["trades"], o["daily"]
        L.append(f"| {k} | {ot.get('n','-')} | {ot.get('expectancy_R','-')} | {ot.get('profit_factor','-')} | "
                 f"{ot.get('net_R','-')} | {od.get('sharpe','-')} | {ot.get('max_drawdown_R','-')} | "
                 f"{al['daily'].get('total_R')} |")
    cr = r["crisis"]
    L += ["", "## Crisis-window diagnostic (crisis alpha)", "",
          f"- {cr['n_windows']} peak->trough index declines >= "
          f"{int(r['config']['crisis_min_decline_pct']*100)}% within "
          f"{r['config']['crisis_max_days']} days",
          f"- worst: " + json.dumps(cr["worst_windows"]),
          f"- **E1 combined R during crises: {cr['R_during_crises']}** "
          f"(outside: {cr['R_outside_crises']}) -> crisis-alpha positive: {cr['crisis_alpha_positive']}",
          f"- buy&hold R during those same windows: {cr['buy_and_hold_R_during_crises']}",
          "", "_" + r["ann_note"] + "_"]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="TREND-SWING daily E1 backtest (research only)")
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--out", default="data/research/trend_swing")
    a = ap.parse_args(argv)
    rep = run(start=a.start, end=a.end, out=a.out)
    print("STATUS:", rep.get("status"))
    if rep.get("status") == "OK":
        v = rep["combined"]["verdict"]
        print("VERDICT (raw combined):", v["verdict"])
        for w in v["why"]:
            print("  -", w)
        for blk in ("combined", "long_only", "short_only"):
            o = rep[blk]["oos"]
            print(f"  {blk:11s} OOS: n={o['trades'].get('n')} expR={o['trades'].get('expectancy_R')} "
                  f"PF={o['trades'].get('profit_factor')} Sharpe={o['daily'].get('sharpe')} "
                  f"maxDD_R={o['trades'].get('max_drawdown_R')}")
        for k, bb in rep["baselines"].items():
            print(f"  BASE {k:13s} OOS Sharpe={bb['oos']['daily'].get('sharpe')} "
                  f"total_R(all)={bb['all']['daily'].get('total_R')}")
    print("files:", rep.get("_files"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
