"""
Research-only, standalone. Backtests 2 candidate features found by comparing
the Fidelity "Technical Analysis for Options" deck + Zerodha Varsity Module 5
against this repo: (1) Bollinger Bands (not implemented anywhere), (2) an
IV-vs-Realized-Vol entry gate (computed in app.optionchain but never used as
a filter). NOT wired into scalp_strategy.py or any live path -- reuses the
canonical backtest engine's own internals (ReplayHarness, decide_from_context,
calibration) exactly as app.backtest.runner.run_backtest() does, but keeps
the raw per-trade signal list so a post-hoc filter can be applied and
compared against the SAME baseline trades, with no engine modification.

Run: venv/bin/python3 data/research/ta_options_pdf_upgrade/backtest_candidates.py
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from app.backtest import calibration
from app.backtest import oi_history_adapter as ad
from app.backtest.replay import ReplayHarness, _mod, _tod_bucket
from app.backtest.runner import _LegCache, _build_decide, _train_samples

_LEG_TFS = ("1m", "3m", "5m", "15m", "30m")


# --------------------------------------------------------------------- #
#  Real underlying data for the 2 candidates (independent of the engine) #
# --------------------------------------------------------------------- #
def bollinger_series(symbol, start, end, *, n=20, k=2.0, tf="5m"):
    """Real underlying OHLC (kind='index') -> {ts: {mid, upper, lower, price}}.
    SMA(n)/stddev(n) of closes, standard n=20/k=2 (Fidelity deck's own
    convention -- no arbitrary tuning)."""
    candles = ad.resample_candles(symbol, tf, kind="index", start=start, end=end).get("candles") or []
    closes = [c["c"] for c in candles]
    out = {}
    for i, c in enumerate(candles):
        if i + 1 < n:
            continue
        window = closes[i + 1 - n:i + 1]
        mid = statistics.mean(window)
        sd = statistics.pstdev(window)
        out[c["t"]] = {"mid": mid, "upper": mid + k * sd, "lower": mid - k * sd,
                       "price": c["c"], "width": (2 * k * sd / mid) if mid else None}
    return out


def realized_vol_series(symbol, start, end, *, n=20, tf="5m", bars_per_year=None):
    """Annualized realized vol from real log returns, n-bar rolling stddev."""
    candles = ad.resample_candles(symbol, tf, kind="index", start=start, end=end).get("candles") or []
    closes = [c["c"] for c in candles]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] and closes[i]]
    # 5m bars, ~75/session (09:15-15:30), ~252 sessions/yr
    bpy = bars_per_year or (75 * 252)
    out = {}
    for i, c in enumerate(candles):
        ri = i - 1   # rets[ri] corresponds to candles[i]
        if ri < n - 1 or ri >= len(rets):
            continue
        window = rets[ri + 1 - n:ri + 1]
        sd = statistics.pstdev(window)
        out[c["t"]] = sd * math.sqrt(bpy)
    return out


def atm_iv_series(symbol, start, end):
    """Real ATM CE/PE IV per cycle timestamp, from the same chain data the
    live engine sees -- {ts: {"CE": iv, "PE": iv}}."""
    out = {}
    for state in ad.iter_market_states(symbol, start, end):
        atm = state.get("atm")
        if atm is None:
            continue
        leg = next((c for c in state["chain"] if c["strike"] == atm), None)
        if not leg:
            continue
        ce_iv = (leg.get("ce") or {}).get("iv")
        pe_iv = (leg.get("pe") or {}).get("iv")
        out[state["ts"]] = {"CE": ce_iv, "PE": pe_iv}
    return out


def _nearest_at_or_before(series: dict, ts: str):
    """series keyed by ISO ts string -- exact-or-before match via sorted keys.
    No look-ahead: never returns a key > ts."""
    keys = _nearest_at_or_before._cache.get(id(series))
    if keys is None:
        keys = sorted(series.keys())
        _nearest_at_or_before._cache[id(series)] = keys
    import bisect
    idx = bisect.bisect_right(keys, ts) - 1
    if idx < 0:
        return None
    return series[keys[idx]]


_nearest_at_or_before._cache = {}


# --------------------------------------------------------------------- #
#  Reuse the canonical engine's own TRAIN -> calibrate -> TEST pipeline  #
# --------------------------------------------------------------------- #
def run_test_replay(symbol, train, test, *, decide_every_sec=30.0):
    """Exactly run_backtest()'s TRAIN/TEST flow, but returns the raw
    ReplayResult (trades + signals) for TEST instead of only aggregated
    metrics, so a post-hoc filter can be applied without touching the
    engine."""
    cfg = {}
    _, samples, wins, losses = _train_samples(symbol, train, cfg, decide_every_sec, False)
    calib = calibration.fit(samples, version=f"pdfupg-{symbol}-{train[0]}_{train[1]}")
    avg_win = round(statistics.mean(wins), 3) if wins else None
    avg_loss = round(statistics.mean(losses), 3) if losses else None
    cache = _LegCache(symbol, test[0], test[1])
    res = ReplayHarness(symbol, test[0], test[1], source="BACKTEST", persist=False,
                        decide_every_sec=decide_every_sec).run(
        _build_decide(calib, avg_win, avg_loss, cfg, cache))
    return res


def _metrics(trades):
    closed = [t for t in trades if t.status == "CLOSED"]
    n = len(closed)
    if n == 0:
        return {"n": 0}
    wins = [t for t in closed if t.outcome == "WIN"]
    losses = [t for t in closed if t.outcome == "LOSS"]
    gw = sum(t.points for t in wins)
    gl = -sum(t.points for t in losses)
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n, 3),
        "net_points": round(sum(t.points for t in closed), 2),
        "expectancy_points": round(sum(t.points for t in closed) / n, 3),
        "profit_factor": round(gw / gl, 3) if gl else None,
    }


def tag_and_filter(res, symbol, test, *, iv_rv_threshold=None):
    """Tag every CLOSED trade with its real Bollinger + IV/RV state at
    entry_ts, then return (baseline_trades, bollinger_breakout_trades,
    iv_rv_cheap_trades)."""
    boll = bollinger_series(symbol, test[0], test[1])
    ivs = atm_iv_series(symbol, test[0], test[1])
    rv = realized_vol_series(symbol, test[0], test[1])
    by_id = {s["signal_id"]: s for s in res.signals}

    closed = [t for t in res.trades if t.status == "CLOSED"]
    boll_pass, ivrv_pass, ivrv_ratios = [], [], []
    for t in closed:
        row = by_id.get(t.signal_id, {})
        entry_ts = t.entry_ts
        b = _nearest_at_or_before(boll, entry_ts)
        side = "CE" if t.opt_type == "CE" else "PE"
        iv_state = _nearest_at_or_before(ivs, entry_ts)
        rv_val = _nearest_at_or_before(rv, entry_ts)
        iv_val = (iv_state or {}).get(side)
        ratio = (iv_val / rv_val) if (iv_val and rv_val) else None
        if ratio is not None:
            ivrv_ratios.append((entry_ts, ratio, t))

        if b:
            px = b["price"]
            if side == "CE" and px > b["upper"]:
                boll_pass.append(t)
            elif side == "PE" and px < b["lower"]:
                boll_pass.append(t)

    if iv_rv_threshold is not None:
        ivrv_pass = [t for _, r, t in ivrv_ratios if r <= iv_rv_threshold]

    return closed, boll_pass, ivrv_pass, ivrv_ratios


def train_iv_rv_threshold(symbol, train):
    """Fit the IV/RV 'cheap' cutoff on TRAIN only (median ratio over TRAIN),
    matching the engine's own no-leakage discipline -- frozen before TEST."""
    boll_unused = None
    ivs = atm_iv_series(symbol, train[0], train[1])
    rv = realized_vol_series(symbol, train[0], train[1])
    ratios = []
    for ts, iv_state in ivs.items():
        rv_val = _nearest_at_or_before(rv, ts)
        for side in ("CE", "PE"):
            iv_val = iv_state.get(side)
            if iv_val and rv_val:
                ratios.append(iv_val / rv_val)
    if not ratios:
        return None
    return statistics.median(ratios)


def run_candidate_check(symbol, train, test, *, label):
    print(f"\n=== {label}: {symbol} TRAIN={train} TEST={test} ===")
    res = run_test_replay(symbol, train, test)
    thr = train_iv_rv_threshold(symbol, train)
    print(f"  IV/RV threshold fit on TRAIN only: {thr}")
    closed, boll_pass, ivrv_pass, ivrv_ratios = tag_and_filter(res, symbol, test, iv_rv_threshold=thr)

    baseline_m = _metrics(closed)
    boll_m = _metrics(boll_pass)
    ivrv_m = _metrics(ivrv_pass)
    print(f"  BASELINE (all TEST trades):        {baseline_m}")
    print(f"  Bollinger-breakout-confirmed only: {boll_m}")
    print(f"  IV/RV<=TRAIN-median only:           {ivrv_m}")
    return {"symbol": symbol, "label": label, "train": list(train), "test": list(test),
           "iv_rv_threshold": thr,
           "baseline": baseline_m, "bollinger_breakout": boll_m, "iv_rv_cheap": ivrv_m,
           "iv_rv_ratio_samples": len(ivrv_ratios)}


if __name__ == "__main__":
    NIFTY_TRAIN = ("2026-07-13", "2026-08-03")
    NIFTY_TEST = ("2026-08-04", "2026-08-28")
    results = []
    results.append(run_candidate_check("NIFTY", NIFTY_TRAIN, NIFTY_TEST, label="forward"))
    # train/test SWAP robustness check
    results.append(run_candidate_check("NIFTY", NIFTY_TEST, NIFTY_TRAIN, label="swapped"))

    NG_TRAIN = ("2026-07-13", "2026-08-03")
    NG_TEST = ("2026-08-04", "2026-08-26")
    results.append(run_candidate_check("NATURALGAS", NG_TRAIN, NG_TEST, label="forward"))
    results.append(run_candidate_check("NATURALGAS", NG_TEST, NG_TRAIN, label="swapped"))

    with open("data/research/ta_options_pdf_upgrade/raw_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nWrote raw_results.json")
