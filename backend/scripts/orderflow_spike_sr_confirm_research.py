"""
Research-only: does filtering the smart-money volume-spike breakout signal
(app.orderflow.smart_money, already shown losing at PF 0.48-0.90 across
every stop/RR reparameterization tried) through the SR Dynamic Engine's
real confirmation (app.sr_dynamic, already built + validated this session)
turn it into a real edge?

Reuses, does not duplicate:
  - app.orderflow.smart_money.smart_money_setups -- the exact spike/outcome
    walk already used by app.orderflow.backtest.
  - app.sr_dynamic.engine.compute_dynamic_sr + app.sr_dynamic.live_state +
    app.sr_dynamic.signal_confirm.evaluate_sr_confirmation -- the exact SR
    confirmation gate already built and wired (opt-in) into the live
    scalp_strategy pipeline earlier this session.

Causal: SR state at a spike's breakout bar is computed from ONLY bars up to
and including that bar (via CandleAggregator, closed-bar semantics) --
never a bar after it.

Chronological TRAIN (first 10 real sessions) / OOS (last 4) split, same
discipline as the RR/stop sweep already run -- report both, never tune on
what gets reported.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import market_hub  # noqa: E402
from app.orderflow import smart_money as sm  # noqa: E402
from app.autoscalp.aggregator import CandleAggregator  # noqa: E402
from app.sr_dynamic.live_state import compute_live_sr_state  # noqa: E402
from app.sr_dynamic.signal_confirm import evaluate_sr_confirmation  # noqa: E402

TRAIN = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07",
        "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14"]
OOS = ["2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18"]


def _epoch(bar):
    from datetime import datetime
    t = bar["bar_start"] if "bar_start" in bar else bar["t"]
    return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp() if isinstance(t, str) else float(t)


def run_session(symbol: str, session: str, *, stop_frac=1.0, rr=3.0) -> list[dict]:
    bars = market_hub.session_bars(symbol, session, tf="5m")
    if not bars:
        return []
    result = sm.smart_money_setups(bars, volume_mult=2.0, rr=rr, stop_frac=stop_frac)
    if result.get("status") != "OK":
        return []

    # build the causal aggregator once, feeding every real bar in order so
    # SR state at ANY breakout_bar only ever sees bars up to that point.
    agg = CandleAggregator(tfs=("5m", "15m", "30m"))
    bar_epoch = {}
    for b in bars:
        ep = _epoch(b)
        bar_epoch[b["bar_start"]] = ep
        for dt, px in ((1.0, b["o"]), (250.0, b["c"])):
            agg.add_tick(ep + dt, px, b.get("v", 0.0) / 2.0)

    rows = []
    for spike in result.get("setups", []):
        for side_key, want, direction in (("buy", "CE", "BULLISH"), ("sell", "PE", "BEARISH")):
            leg = spike.get(side_key)
            if not leg or not leg.get("breakout_bar"):
                continue
            oc = leg.get("outcome") or {}
            if oc.get("status") in (None, "PENDING", "TRIGGERED"):
                continue
            bb_epoch = bar_epoch.get(leg["breakout_bar"])
            if bb_epoch is None:
                continue
            bars_by_tf = agg.snapshot(now_epoch=bb_epoch + 260)
            live_sr = compute_live_sr_state(symbol, bars_by_tf)
            atr = None
            H = [x["h"] for x in bars_by_tf.get("5m") or []]
            L = [x["l"] for x in bars_by_tf.get("5m") or []]
            if len(H) >= 15:
                from app.engines.sr_engine import _atr
                atr = _atr(H, L, [x["c"] for x in bars_by_tf.get("5m") or []], len(H), 14)
            verdict = evaluate_sr_confirmation(want, live_sr, index_move_pts=atr or leg["risk_points"])
            points = float(oc.get("points", 0.0))
            rows.append({"session": session, "side": leg["side"], "points": points,
                        "sr_verdict": verdict["verdict"]})
    return rows


def _stats(rows):
    pts = [r["points"] for r in rows]
    if not pts:
        return {"n": 0}
    win = [p for p in pts if p > 0]
    loss = [p for p in pts if p < 0]
    pf = round(sum(win) / abs(sum(loss)), 3) if loss else None
    return {"n": len(pts), "win_rate_pct": round(100.0 * len(win) / len(pts), 1),
            "expectancy": round(mean(pts), 4), "profit_factor": pf, "net": round(sum(pts), 2)}


def main():
    for symbol in ("NATURALGAS",):
        for split_name, sessions in (("TRAIN", TRAIN), ("OOS", OOS)):
            all_rows = []
            for s in sessions:
                all_rows.extend(run_session(symbol, s))
            baseline = _stats(all_rows)
            confirmed = _stats([r for r in all_rows if r["sr_verdict"] == "CONFIRM"])
            not_contra = _stats([r for r in all_rows if r["sr_verdict"] != "CONTRADICT"])
            by_verdict = {}
            for r in all_rows:
                by_verdict.setdefault(r["sr_verdict"], []).append(r)
            print(f"=== {symbol} {split_name} ===")
            print("  baseline (all spikes):        ", baseline)
            print("  SR-CONFIRM only:               ", confirmed)
            print("  SR != CONTRADICT (drop bad only):", not_contra)
            for v, rs in by_verdict.items():
                print(f"    verdict={v}: {_stats(rs)}")
            print()


if __name__ == "__main__":
    main()
