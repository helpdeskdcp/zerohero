"""
Walk-forward, no-lookahead backtest of the PRODUCTION raw-signal generator
(app.engines.sr_engine.compute_sr + app.engines.state_classifier.classify --
the exact functions app.engines.scalp_strategy.decide_from_context calls
before any option-chain-dependent stage) against REAL NATGAS MCX futures
1-minute data.

DATA SOURCE (real, already captured, not synthetic):
data/historical/upstox_v3_validation/raw_futures_windows_NATURALGAS_FUT_25_SEP_26_2026-09-10.json.gz
Upstox v3 API pull of the NATURALGAS FUT 25-SEP-26 contract, 1-minute OHLCV+OI.
Usable range 2026-04-24 -> 2026-09-10 (~4.5 months, 64,230 real candles; two
earlier windows, 2026-02-27 and 2026-03-27, came back empty and are dropped).

SCOPE / HONEST LIMITATION -- read before trusting these numbers:
This validates the RAW DIRECTIONAL signal (SUPPORT_BREAKDOWN / SUPPORT_REVERSAL
/ RESISTANCE_BREAKOUT / RESISTANCE_REVERSAL -> BULLISH/BEARISH) against real
NATGAS FUTURES price action, entirely in underlying-futures points.

It does NOT reproduce the option-premium P&L NATGAS actually trades in
production. In live decide_from_context(), entry/stop_loss/target_1/target_2
(_plan_from_leg) are sized off the SELECTED OPTION LEG's own ATR (via
analyse_leg on that leg's own candles), not the underlying's ATR -- and no
multi-month real NATGAS option-premium history exists anywhere in this system
(only ~10 real days via the live histcap capture, per this session's
established data-ceiling). Building a true premium-level walk-forward would
need either months more live capture, or a synthetic BS+assumed-IV pricing
layer -- this script deliberately does not fabricate the latter. This is the
same mechanism-vs-performance distinction already applied to every other
backtest this session (MTF Cascade, Strategy Verification Engine).

Because there is no historical option chain for this window, every call
below passes chain=None -- compute_sr()/classify() already support this
(chain is an optional kwarg in both, verified by reading the source, not
assumed).

The exit/trailing simulation mirrors app.engines.paper_trading.py's exact
formula (profit-lock at MFE>=0.6R -> entry+0.2R, MFE>=1.0R -> entry+0.5R;
separate flat-distance trail ratchet once MFE>=trail_dist; hit_sl/hit_t1
checks; hard time-stop) applied to underlying points using the SAME
sl_atr/t1_atr/t2_atr/trail_atr multiples decide_from_context would use
(NATGAS's own symbol_profile only overrides trail_atr and max_hold_sec --
sl_atr/t1_atr/t2_atr stay at the global 1.1/1.7/2.6 defaults, confirmed by
reading app/autoscalp/runner.py's symbol_profiles).
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.autoscalp.aggregator import CandleAggregator
from app.backtest.replay import _mod, _tod_bucket
from app.engines.regime_mtf import detect_regime
from app.engines.sr_engine import compute_sr
from app.engines.state_classifier import classify

DATA_FILE = (Path(__file__).parents[1] / "data" / "historical" / "upstox_v3_validation" /
             "raw_futures_windows_NATURALGAS_FUT_25_SEP_26_2026-09-10.json.gz")
OUT_DIR = Path(__file__).parents[1] / "data" / "research" / "natgas_futures_backtest"

# NATGAS's actual production symbol_profile (app/autoscalp/runner.py:240-242)
# only overrides these two; sl_atr/t1_atr/t2_atr stay at the global defaults
# used by _plan_from_leg in app/engines/scalp_strategy.py:237-239,251.
SL_ATR, T1_ATR, T2_ATR = 1.1, 1.7, 2.6
TRAIL_ATR = 1.6          # NATGAS override (global default is 0.9)
MAX_HOLD_SEC = 1800.0    # NATGAS override (global default is 1500)

BLOCK_SIGNAL_TYPES = {"RESISTANCE_BREAKOUT"}   # scalp_strategy.py _DEFAULT_FILTERS
BLOCK_REGIMES = {"UNSTABLE"}
BLOCK_TOD = {"AFTERNOON"}

TRAIN_END = "2026-06-30"
VALIDATION_END = "2026-07-31"
# everything after VALIDATION_END is OOS


def _load_real_candles():
    with gzip.open(DATA_FILE) as f:
        d = json.load(f)
    rows = {}
    for win in d["windows"]:
        for c in (win.get("body", {}).get("data", {}).get("candles") or []):
            ts, o, h, l, cl, v = c[0], c[1], c[2], c[3], c[4], c[5]
            dt = datetime.fromisoformat(ts)
            epoch = dt.timestamp()
            rows[epoch] = (epoch, float(o), float(h), float(l), float(cl), float(v))
    ordered = [rows[k] for k in sorted(rows)]
    return ordered


def _session_date_ist(epoch: float) -> str:
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.fromtimestamp(epoch, ist).date().isoformat()


def _simulate_outcome(direction: str, entry: float, atr: float, future_5m: list[dict]):
    """Mirrors app/engines/paper_trading.py's update_trade_price/_excursions
    exactly (profit-lock ladder, flat-distance trail ratchet, hit_sl/hit_t1,
    hard time-stop), applied to underlying points instead of option premium.
    `future_5m` are REAL closed 5m bars strictly AFTER the signal bar -- using
    real future bars here is the outcome-simulation step (same as every other
    backtest this session), not a look-ahead violation of signal generation."""
    sign = 1 if direction == "BULLISH" else -1
    risk = SL_ATR * atr
    sl = entry - sign * risk
    t1 = entry + sign * T1_ATR * atr
    t2 = entry + sign * T2_ATR * atr
    trail_dist = TRAIL_ATR * atr

    mfe = mae = 0.0
    elapsed = 0.0
    for bar in future_5m:
        elapsed += 300.0
        for px in (bar["o"], bar["h"], bar["l"], bar["c"]) if sign == 1 else (bar["o"], bar["l"], bar["h"], bar["c"]):
            fav = sign * (px - entry)
            adv = -fav
            mfe = max(mfe, fav)
            mae = max(mae, adv)

            hit_sl = (sign == 1 and px <= sl) or (sign == -1 and px >= sl)
            hit_t1 = (sign == 1 and px >= t1) or (sign == -1 and px <= t1)
            if hit_sl or hit_t1:
                stop_moved_fav = (sign == 1 and sl > entry) or (sign == -1 and sl < entry)
                if hit_t1:
                    reason, points = "TARGET", sign * (t1 - entry)
                elif stop_moved_fav:
                    reason, points = "TRAIL", sign * (sl - entry)
                else:
                    reason, points = "STOP", sign * (sl - entry)
                return {"exit_reason": reason, "points": round(points, 4), "mfe": round(mfe, 4),
                        "mae": round(mae, 4), "risk": round(risk, 4), "t1_dist": round(T1_ATR * atr, 4),
                        "t2_dist": round(T2_ATR * atr, 4), "t2_touched": mfe >= T2_ATR * atr,
                        "holding_sec": elapsed}

            # profit-lock ladder (paper_trading.py:176-190)
            mfe_r = mfe / risk if risk > 0 else 0.0
            lock = None
            if mfe_r >= 1.0:
                lock = 0.5 * risk
            elif mfe_r >= 0.6:
                lock = 0.2 * risk
            if lock is not None:
                cand_sl = entry + sign * lock
                if (sign == 1 and cand_sl > sl) or (sign == -1 and cand_sl < sl):
                    sl = cand_sl
            # flat trail ratchet (paper_trading.py:191-194)
            if mfe >= trail_dist:
                cand_sl = px - sign * trail_dist
                if (sign == 1 and cand_sl > sl) or (sign == -1 and cand_sl < sl):
                    sl = cand_sl

        if elapsed >= MAX_HOLD_SEC:
            last_c = future_5m[future_5m.index(bar)]["c"]
            return {"exit_reason": "TIME", "points": round(sign * (last_c - entry), 4),
                    "mfe": round(mfe, 4), "mae": round(mae, 4), "risk": round(risk, 4),
                    "t1_dist": round(T1_ATR * atr, 4), "t2_dist": round(T2_ATR * atr, 4),
                    "t2_touched": mfe >= T2_ATR * atr, "holding_sec": elapsed}

    if not future_5m:
        return None
    last_c = future_5m[-1]["c"]
    return {"exit_reason": "END_OF_DATA", "points": round(sign * (last_c - entry), 4),
            "mfe": round(mfe, 4), "mae": round(mae, 4), "risk": round(risk, 4),
            "t1_dist": round(T1_ATR * atr, 4), "t2_dist": round(T2_ATR * atr, 4),
            "t2_touched": mfe >= T2_ATR * atr, "holding_sec": elapsed}


def run():
    candles = _load_real_candles()
    print(f"loaded {len(candles)} real 1m candles, "
          f"{_session_date_ist(candles[0][0])} -> {_session_date_ist(candles[-1][0])}")

    agg = CandleAggregator(tfs=("5m", "15m", "30m"))
    # CandleAggregator's internal deques are capped at 400 bars (fine for the
    # live system's memory budget). This backtest spans ~4.5 months, so it
    # keeps its OWN unbounded, append-only history of every closed 5m bar for
    # the outcome-simulation "future bars" lookup -- indexing into the capped
    # deque instead would silently go wrong once the deque starts evicting.
    all_5m: list[dict] = []
    signals = []
    last_signal_bucket = None

    for epoch, o, h, l, c, v in candles:
        # replay each real 1m bar as its own o->l->h->c micro-path (same
        # technique CandleAggregator.seed_from_ohlc already uses) so every
        # timeframe's OHLC stays faithful, not just its close.
        path = (o, l, h, c) if h - l >= 0 else (o, h, l, c)
        sub = [epoch + 1.0, epoch + 20.0, epoch + 40.0, epoch + 58.0]
        for t, px in zip(sub, path):
            agg.add_tick(t, px, v / 4.0)

        closed_5m = agg.bars("5m", now_epoch=epoch)
        if not closed_5m:
            continue
        newest_bucket = closed_5m[-1]["t"]
        if newest_bucket == last_signal_bucket:
            continue          # already evaluated this closed bar
        last_signal_bucket = newest_bucket
        all_5m.append(closed_5m[-1])

        bars_by_tf = agg.snapshot(now_epoch=epoch)
        sr = compute_sr(bars_by_tf, chain=None, mode="index", config={})
        if sr.get("status") != "OK":
            continue
        st = classify(bars_by_tf, sr, chain=None, config={})
        if st["state"] == "NONE":
            continue

        reg = detect_regime(bars_by_tf, config={})
        tod = _tod_bucket(_mod(datetime.fromtimestamp(epoch, timezone(timedelta(hours=5, minutes=30))).isoformat()))

        blocked = (st["state"] in BLOCK_SIGNAL_TYPES or reg["regime"] in BLOCK_REGIMES or tod in BLOCK_TOD)

        signals.append({
            "epoch": epoch, "bucket": newest_bucket, "session_date": _session_date_ist(epoch),
            "signal_type": st["state"], "direction": st["direction"], "regime": reg["regime"],
            "tod_bucket": tod, "blocked": blocked, "entry": closed_5m[-1]["c"], "atr": sr.get("atr"),
            "bar_index": len(all_5m) - 1,
        })

    print(f"raw signals found: {len(signals)} (blocked-by-live-filters: "
          f"{sum(1 for s in signals if s['blocked'])})")

    # outcome simulation: walk REAL forward 5m bars (this is the outcome step,
    # not signal generation -- using real future bars here is correct, see
    # module docstring)
    resolved = []
    for s in signals:
        if s["atr"] is None or s["atr"] <= 0:
            continue
        future = all_5m[s["bar_index"] + 1:]
        outcome = _simulate_outcome(s["direction"], s["entry"], s["atr"], future)
        if outcome is None:
            continue
        resolved.append({**s, **outcome})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "natgas_futures_backtest_rows.json", "w") as f:
        json.dump(resolved, f, indent=2)
    # persisted so a target-distance sensitivity sweep can re-simulate
    # path-correctly (real bar order) instead of approximating from MFE/MAE.
    with open(OUT_DIR / "natgas_futures_5m_bars.json", "w") as f:
        json.dump(all_5m, f)
    with open(OUT_DIR / "natgas_futures_signals.json", "w") as f:
        json.dump(signals, f)

    return resolved


if __name__ == "__main__":
    rows = run()
    print(f"resolved (outcome available): {len(rows)}")
