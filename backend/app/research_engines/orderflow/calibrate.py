"""
Optimal-retracement-zone CALIBRATION.  TRAIN -> VALIDATION -> OOS.

For each retracement zone z in cfg['entry_zones'], replay every signal through
the entry-timing engine at that z and the exit engine, then aggregate:
  fill_rate, n, win_rate, avg_R, expectancy_R, MAE_R, MFE_R, sl_rate,
  t1/t2/t3 hit rate.
z* is chosen on TRAIN by expectancy_R (subject to a sample floor) and must ALSO
be positive on VALIDATION. OOS is never used to choose z. Nothing is hard-coded.
"""
from __future__ import annotations

from . import entry_timing as ET
from . import exit_engine as EX


def _zone_stats(bars, frame, signals, cfg, z, session_filter=None):
    fills = 0
    trades = []
    n_sig = 0
    for s in signals:
        if session_filter and bars[s["i"]]["session_date"] not in session_filter:
            continue
        n_sig += 1
        r = ET.run(bars, frame, s["i"], s["direction"], s["rk_score"], cfg, zone_frac=z)
        if r["status"] != "ENTRY_READY":
            continue
        fills += 1
        ex = EX.simulate(bars, r, cfg)
        trades.append({**r, **ex, "year": r["session_date"][:4]})
    n = len(trades)
    if not n:
        return {"zone": z, "n_signals": n_sig, "fill_rate": 0.0, "n": 0}
    wins = [t for t in trades if t["win"]]
    R = [t["r_multiple"] for t in trades]
    gl = sum(t["points"] for t in trades if t["points"] <= 0)
    gw = sum(t["points"] for t in trades if t["points"] > 0)
    return {
        "zone": z, "n_signals": n_sig, "fill_rate": round(fills / max(1, n_sig), 3),
        "n": n, "win_rate": round(len(wins) / n, 4),
        "avg_R": round(sum(R) / n, 4), "expectancy_R": round(sum(R) / n, 4),
        "net_points": round(sum(t["points"] for t in trades), 1),
        "profit_factor": round(gw / abs(gl), 3) if gl < 0 else 9.99,
        "mae_R": round(sum(t["mae_R"] for t in trades) / n, 3),
        "mfe_R": round(sum(t["mfe_R"] for t in trades) / n, 3),
        "sl_rate": round(sum(t["sl_rate"] for t in trades) / n, 4),
        "t1_rate": round(sum(1 for t in trades if t["t1_hit"]) / n, 4),
        "t2_rate": round(sum(1 for t in trades if t["t2_hit"]) / n, 4),
        "t3_rate": round(sum(1 for t in trades if t["t3_hit"]) / n, 4),
        "_trades": trades,
    }


def calibrate(bars, frame, signals, cfg, train_sessions, val_sessions) -> dict:
    zones = cfg["entry_zones"]
    train_tbl = {z: _zone_stats(bars, frame, signals, cfg, z, set(train_sessions)) for z in zones}
    val_tbl = {z: _zone_stats(bars, frame, signals, cfg, z, set(val_sessions)) for z in zones}

    floor = cfg["min_trades_per_zone"]
    # TRAIN candidates: enough trades, ranked by expectancy_R
    cand = sorted(
        [z for z in zones if train_tbl[z].get("n", 0) >= floor],
        key=lambda z: train_tbl[z]["expectancy_R"], reverse=True)
    chosen = None
    reason = ""
    for z in cand:
        v = val_tbl[z]
        if (v.get("n", 0) >= max(15, floor // 2) and v.get("net_points", -1) > 0
                and v.get("profit_factor", 0) >= 1.10 and v.get("expectancy_R", -9) > 0.02):
            chosen = z
            reason = (f"best TRAIN expectancy among n>={floor} zones that is also "
                      f"VALIDATION-positive (train expR {train_tbl[z]['expectancy_R']}, "
                      f"val expR {v['expectancy_R']})")
            break
    if chosen is None:
        # nothing survives -> pick the least-bad TRAIN zone but flag it
        chosen = cand[0] if cand else cfg["entry_zone_frac"]
        reason = ("NO zone is positive on both TRAIN and VALIDATION -> engine is "
                  "NOT VALIDATED; z* is the least-bad TRAIN zone, reported for record only")

    def strip(tbl):
        return {z: {k: v for k, v in d.items() if k != "_trades"} for z, d in tbl.items()}

    return {
        "chosen_zone": chosen, "reason": reason,
        "train_table": strip(train_tbl), "val_table": strip(val_tbl),
        "candidates_ranked": cand,
    }
