"""
Live-SR-wiring spec sections 14-15: historical validation recompute (zones/
touches/rejections/day) for all 5 requested symbols, and a before/after
backtest of the SR gate's impact on the raw signal stream.

Data availability (real only, never fabricated):
  NATGAS:     3.75mo, already built this session (natgas_futures_backtest)
  NIFTY:      1yr Kaggle 5m index bars (tail-trimmed, see sr_dynamic_validation.py)
  BANKNIFTY:  ~3mo real MCX... no, NFO futures (Upstox v3, already in-repo)
  CRUDEOIL:   ~4mo real MCX futures (Upstox v3, already in-repo)
  SENSEX:     NO real historical futures/option data exists anywhere in this
              repo (checked data/historical/upstox_v3_validation/ -- no
              SENSEX file was ever pulled). Reported as a hard data ceiling,
              not skipped silently.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.autoscalp.aggregator import CandleAggregator
from app.engines.sr_engine import compute_sr
from app.engines.state_classifier import classify
from app.liquidity_sweep.backtest import load_kaggle_nifty_bars
from app.sr_dynamic.engine import compute_dynamic_sr
from app.sr_dynamic.live_state import compute_live_sr_state
from app.sr_dynamic.signal_confirm import evaluate_sr_confirmation

UPSTOX_DIR = Path(__file__).parents[1] / "data" / "historical" / "upstox_v3_validation"
NATGAS_5M = (Path(__file__).parents[1] / "data" / "research" / "natgas_futures_backtest" /
            "natgas_futures_5m_bars.json")


def _load_upstox_1m(filename: str) -> list[dict]:
    with gzip.open(UPSTOX_DIR / filename) as f:
        d = json.load(f)
    rows = {}
    for win in d["windows"]:
        for c in (win.get("body", {}).get("data", {}).get("candles") or []):
            ts, o, h, l, cl, v = c[0], c[1], c[2], c[3], c[4], c[5]
            from datetime import datetime
            epoch = datetime.fromisoformat(ts).timestamp()
            rows[epoch] = {"t": ts, "o": float(o), "h": float(h), "l": float(l), "c": float(cl), "v": float(v)}
    return [rows[k] for k in sorted(rows)]


def _resample_1m_to_5m(bars_1m: list[dict], factor: int = 5) -> list[dict]:
    """Simple non-overlapping bucketing -- deliberately NOT routed through
    CandleAggregator here: its deques are capped at 400 bars (fine for the
    live system's memory budget, but this script found -- the same way
    scripts/natgas_futures_backtest.py already did -- that it silently
    truncates a multi-month resample down to the last ~3 trading days)."""
    out = []
    for i in range(0, len(bars_1m) - factor + 1, factor):
        chunk = bars_1m[i:i + factor]
        out.append({
            "t": chunk[0]["t"], "o": chunk[0]["o"], "c": chunk[-1]["c"],
            "h": max(b["h"] for b in chunk), "l": min(b["l"] for b in chunk),
            "v": sum(b.get("v", 0.0) for b in chunk),
        })
    return out


def n_trading_days(bars_5m: list[dict]) -> int:
    return len({b["t"][:10] for b in bars_5m})


def touches_per_day(bars_5m: list[dict]) -> dict:
    zones = compute_dynamic_sr({"5m": bars_5m}, primary_tf="5m")
    if not zones:
        return {"status": "no_zones"}
    days = n_trading_days(bars_5m)
    total_touches = sum(z["touches"] for z in zones)
    total_rejections = sum(z["rejections"] for z in zones)
    return {
        "status": "ok", "n_zones": len(zones), "trading_days": days,
        "total_touches": total_touches, "total_rejections": total_rejections,
        "touches_per_day": round(total_touches / days, 2) if days else None,
        "rejections_per_day": round(total_rejections / days, 2) if days else None,
        "reaction_rate_pct": round(100.0 * total_rejections / total_touches, 1) if total_touches else None,
    }


def signal_impact_before_after(bars_5m: list[dict], *, decide_every_n_bars: int = 3) -> dict:
    """Reuses the exact same causal walk-forward methodology as
    scripts/natgas_futures_backtest.py (compute_sr+classify, chain=None) to
    generate the raw directional signal stream, then applies the SR gate as
    a POST-HOC filter (evaluate_sr_confirmation) to compare signal counts
    before vs after -- an honest before/after on the SAME sample, not a
    re-tuned one."""
    agg = CandleAggregator(tfs=("5m", "15m", "30m"))
    from datetime import datetime
    raw_signals = []
    last_bucket = None
    for i, b in enumerate(bars_5m):
        ep = datetime.fromisoformat(b["t"]).timestamp() if "T" in str(b["t"]) else float(b["t"])
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
        want = "CE" if st["state"] in ("SUPPORT_REVERSAL", "RESISTANCE_BREAKOUT") else "PE"
        live_sr = compute_live_sr_state("SYM", bars_by_tf)
        index_move_pts = abs(sr["price"] - st["anchor"]["level"]) + (sr["atr"] or 0)
        verdict = evaluate_sr_confirmation(want, live_sr, index_move_pts=index_move_pts)
        raw_signals.append({"signal_type": st["state"], "want": want, "sr_verdict": verdict["verdict"]})

    total = len(raw_signals)
    contradicted = sum(1 for s in raw_signals if s["sr_verdict"] == "CONTRADICT")
    confirmed = sum(1 for s in raw_signals if s["sr_verdict"] == "CONFIRM")
    insufficient = sum(1 for s in raw_signals if s["sr_verdict"] == "INSUFFICIENT")
    neutral = total - contradicted - confirmed - insufficient
    return {
        "total_raw_signals": total,
        "before_sr_gate": total,
        "after_sr_gate_marginal_veto_only": total - contradicted,   # if every CONTRADICT were marginal
        "sr_confirm": confirmed, "sr_contradict": contradicted,
        "sr_neutral": neutral, "sr_insufficient": insufficient,
        "pct_would_be_filtered_if_all_marginal": round(100.0 * contradicted / total, 1) if total else None,
    }


def main():
    report = {}

    print("NATGAS...")
    natgas_bars = json.load(open(NATGAS_5M))
    report["NATGAS"] = {"touches_per_day": touches_per_day(natgas_bars),
                        "signal_impact": signal_impact_before_after(natgas_bars)}

    print("NIFTY...")
    nifty_bars = load_kaggle_nifty_bars(limit_years=1.0)
    last_real = max(i for i, b in enumerate(nifty_bars) if b["h"] != b["l"])
    nifty_bars = nifty_bars[:last_real + 1]
    report["NIFTY"] = {"touches_per_day": touches_per_day(nifty_bars),
                       "signal_impact": signal_impact_before_after(nifty_bars)}

    print("BANKNIFTY...")
    bn_1m = _load_upstox_1m("raw_futures_windows_BANKNIFTY_FUT_29_SEP_26_2026-09-10.json.gz")
    bn_5m = _resample_1m_to_5m(bn_1m)
    report["BANKNIFTY"] = {"touches_per_day": touches_per_day(bn_5m),
                           "signal_impact": signal_impact_before_after(bn_5m)}

    print("CRUDEOIL...")
    co_1m = _load_upstox_1m("raw_futures_windows_CRUDEOIL_FUT_21_SEP_26_2026-09-11.json.gz")
    co_5m = _resample_1m_to_5m(co_1m)
    report["CRUDEOIL"] = {"touches_per_day": touches_per_day(co_5m),
                          "signal_impact": signal_impact_before_after(co_5m)}

    report["SENSEX"] = {"status": "NO_REAL_DATA",
                        "reason": "no SENSEX historical futures/option dataset exists anywhere in this "
                                  "repo (checked data/historical/upstox_v3_validation/ and Kaggle) -- "
                                  "not fabricated, reported as a hard data ceiling"}

    out_path = Path(__file__).parents[1] / "data" / "research" / "sr_dynamic" / "live_wiring_validation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    print(f"\nsaved to {out_path}")


if __name__ == "__main__":
    main()
