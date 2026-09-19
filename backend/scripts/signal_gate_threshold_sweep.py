"""
Walk-forward, out-of-sample threshold sweep for the high-confidence
single-signal gate (app.signal_gate.final_signal_gate), reusing every
piece already built and validated this session rather than a new backtest
engine:

  - raw signal generation: app.engines.sr_engine.compute_sr +
    app.engines.state_classifier.classify (chain=None), the same causal
    walk exactly as scripts/natgas_futures_backtest.py and
    scripts/sr_live_wiring_validation.py.
  - SR confirmation: app.sr_dynamic.signal_confirm.evaluate_sr_confirmation
    against the same real bars.
  - confidence scoring: app.signal_gate.final_signal_gate.evaluate_final_signal
    itself (called with min_signal_score=0 so every signal that clears the
    hard SR/RR gates gets scored, then bucketed by threshold afterward --
    avoids recomputing the walk for each candidate threshold).
  - outcome simulation: the exact ATR-based trailing/exit mechanics from
    scripts/natgas_futures_backtest.py's _simulate_outcome, applied to
    underlying points (see that script's docstring for the honest
    mechanism-vs-premium-P&L scope limitation -- unchanged here).

SCOPE: this sweeps the SCORE threshold only (70/75/80/85/90/95), holding
every other gate setting (require_sr_confirmation=True, min_rr=1.5,
one_active_signal=True) fixed. It reports A) raw signals, B) SR-confirmed
signals (require_sr_confirmation True, no score filter), C) each score
threshold's approved count and simulated outcome stats. Nothing here
selects a "winning" threshold or writes it back into any config --
per instruction, the threshold is not tuned on the same sample used to
report performance; this produces the report for a human decision.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).parents[1]))

import scripts.natgas_futures_backtest as ngb
from app.autoscalp.aggregator import CandleAggregator
from app.engines.sr_engine import compute_sr
from app.engines.state_classifier import classify
from app.liquidity_sweep.backtest import load_kaggle_nifty_bars
from app.signal_gate.final_signal_gate import evaluate_final_signal
from app.sr_dynamic.live_state import compute_live_sr_state
from app.sr_dynamic.signal_confirm import evaluate_sr_confirmation
from scripts.sr_live_wiring_validation import (
    _load_upstox_1m,
    _resample_1m_to_5m,
)

NATGAS_5M = (Path(__file__).parents[1] / "data" / "research" / "natgas_futures_backtest" /
            "natgas_futures_5m_bars.json")

THRESHOLDS = [70.0, 75.0, 80.0, 85.0, 90.0, 95.0]


def _epoch(t):
    if isinstance(t, (int, float)):
        return float(t)
    return datetime.fromisoformat(str(t)).timestamp()


def walk_forward_candidates(bars_5m: list[dict], *, decide_every_n_bars: int = 3) -> list[dict]:
    """Every real, causal candidate signal: raw state -> SR confirmation ->
    confidence score (min_signal_score=0 so nothing is filtered by score
    here) -> real ATR-based outcome simulation. One row per candidate."""
    agg = CandleAggregator(tfs=("5m", "15m", "30m"))
    rows = []
    for i, b in enumerate(bars_5m):
        ep = _epoch(b["t"])
        for dt, px in ((1.0, b["o"]), (250.0, b["c"])):
            agg.add_tick(ep + dt, px, b.get("v", 0.0) / 2.0)
        if i % decide_every_n_bars:
            continue
        bars_by_tf = agg.snapshot(now_epoch=ep + 260)
        sr = compute_sr(bars_by_tf, chain=None, mode="index")
        if sr.get("status") != "OK":
            continue
        st = classify(bars_by_tf, sr, chain=None)
        if st["state"] == "NONE":
            continue
        direction = st["direction"]
        want = "CE" if direction == "BULLISH" else "PE"
        atr = sr.get("atr")
        if not atr or atr <= 0:
            continue

        live_sr = compute_live_sr_state("SYM", bars_by_tf)
        index_move_pts = abs(sr["price"] - st["anchor"]["level"]) + atr
        sr_conf = evaluate_sr_confirmation(want, live_sr, index_move_pts=index_move_pts)

        entry = sr["price"]
        sign = 1 if direction == "BULLISH" else -1
        risk = ngb.SL_ATR * atr
        rr = ngb.T1_ATR / ngb.SL_ATR         # target distance / risk distance, same convention as the gate's rr field

        decision = {"decision": f"BUY_{want}", "symbol": "SYM", "direction": direction,
                   "signal_type": st["state"], "rr": rr, "strike": None, "expiry": None,
                   "support": sr.get("support", {}).get("level"), "resistance": sr.get("resistance", {}).get("level"),
                   "component_scores": {"htf": st["components"].get("htf"), "vwap": st["components"].get("vwap"),
                                        "momentum": st["components"].get("momentum"),
                                        "volume": st["components"].get("volume"),
                                        "oi": st["components"].get("oi")},
                   "calibration_status": "prior", "calibration_samples": 0}
        fsg = evaluate_final_signal(decision, sr_confirmation=sr_conf, chain_bias=None,
                                    min_signal_score=0.0, require_sr_confirmation=True,
                                    one_active_signal=False)

        outcome = ngb._simulate_outcome(direction, entry, atr, bars_5m[i + 1:i + 1 + 400])
        rows.append({"epoch": ep, "signal_type": st["state"], "direction": direction,
                    "sr_verdict": sr_conf["verdict"], "confidence_score": fsg.confidence_score,
                    "gate_state": fsg.state, "outcome": outcome})
    return rows


def _stats_for(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    pts = [r["outcome"]["points"] for r in rows if r["outcome"]]
    win = [p for p in pts if p > 0]
    loss = [p for p in pts if p < 0]
    reasons = {}
    for r in rows:
        rr = (r["outcome"] or {}).get("exit_reason", "NA")
        reasons[rr] = reasons.get(rr, 0) + 1
    pf = round(sum(win) / abs(sum(loss)), 3) if loss else None
    return {
        "n": len(pts), "win_rate_pct": round(100.0 * len(win) / len(pts), 1) if pts else None,
        "expectancy": round(mean(pts), 4) if pts else None,
        "median_r_proxy": round(median(pts), 4) if pts else None,
        "profit_factor": pf,
        "exit_reason_pct": {k: round(100.0 * v / len(rows), 1) for k, v in reasons.items()},
    }


def run_instrument(name: str, bars_5m: list[dict]) -> dict:
    rows = walk_forward_candidates(bars_5m)
    sr_confirmed = [r for r in rows if r["sr_verdict"] == "CONFIRM"]
    result = {
        "instrument": name, "n_bars_5m": len(bars_5m),
        "raw_signals": len(rows),
        "sr_confirmed_signals": len(sr_confirmed),
        "raw_stats": _stats_for(rows),
        "sr_confirmed_stats": _stats_for(sr_confirmed),
        "by_threshold": {},
    }
    for t in THRESHOLDS:
        subset = [r for r in sr_confirmed if r["confidence_score"] is not None and r["confidence_score"] >= t]
        result["by_threshold"][str(t)] = {"n_approved": len(subset), **_stats_for(subset)}
    return result


def main():
    report = {}

    print("NATGAS...")
    natgas_bars = json.load(open(NATGAS_5M))
    report["NATGAS"] = run_instrument("NATGAS", natgas_bars)

    print("NIFTY...")
    nifty_bars = load_kaggle_nifty_bars(limit_years=1.0)
    last_real = max(i for i, b in enumerate(nifty_bars) if b["h"] != b["l"])
    nifty_bars = nifty_bars[:last_real + 1]
    report["NIFTY"] = run_instrument("NIFTY", nifty_bars)

    print("BANKNIFTY...")
    bn_1m = _load_upstox_1m("raw_futures_windows_BANKNIFTY_FUT_29_SEP_26_2026-09-10.json.gz")
    report["BANKNIFTY"] = run_instrument("BANKNIFTY", _resample_1m_to_5m(bn_1m))

    print("CRUDEOIL...")
    co_1m = _load_upstox_1m("raw_futures_windows_CRUDEOIL_FUT_21_SEP_26_2026-09-11.json.gz")
    report["CRUDEOIL"] = run_instrument("CRUDEOIL", _resample_1m_to_5m(co_1m))

    report["SENSEX"] = {"status": "NO_REAL_DATA",
                        "reason": "no SENSEX historical futures/option dataset exists anywhere in this repo"}

    out_path = Path(__file__).parents[1] / "data" / "research" / "signal_gate" / "threshold_sweep.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    print(f"\nsaved to {out_path}")


if __name__ == "__main__":
    main()
