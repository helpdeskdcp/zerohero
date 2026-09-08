"""
Dynamic trade-management simulator.  RESEARCH ONLY -- simulates on historical
bars, never touches a broker or the live SL/target code.

Per in-trade bar it produces three CALIBRATED probabilities (small logits fitted
on the walk-forward TRAIN span, applied OOS -- not hand-tuned confidences):

  TargetAchievementProbability   P(hit T1 before SL within the remaining hold)
  SLThreatProbability            P(hit SL before T1 within the next few bars)
  RecoveryProbability            P(return to entry before SL | currently adverse)

Exit policies, simulated SEPARATELY (spec):
  fixed_sl     initial SL, never moved. Exit: SL | T1 | max_hold.
  early_exit   fixed SL + : exit if SLThreat>=hi AND Recovery<hi ;
               or if in profit (MFE>=profit_lock) AND pressure slope flips adverse.
  trailing_sl  after `trail_confirm_bars` of confirmed favourable pressure,
               tighten SL to close -/+ trail_atr*atr. SL is ONLY ever tightened.

Records per trade: r_multiple, points, mae, mfe (in R), exit_reason,
recovered_before_sl (price revisited entry after an adverse excursion, pre-SL).
"""
from __future__ import annotations

import math

from .config import merged
from .formulas import atr

_TM_FEATS_THREAT = ["dist_sl_atr", "adverse_net", "adverse_accel", "opt_deterioration",
                    "oi_deterioration", "mtf_deterioration", "bars_held_frac"]
_TM_FEATS_RECOV = ["opp_net_increase", "wick_rejection", "pressure_reversal",
                   "opt_confirmation", "oi_reversal", "align_1m_3m"]
_TM_FEATS_TARGET = ["fav_net", "fav_persistence", "mfe_atr", "dist_t1_atr", "bars_left_frac"]


def _logit_fit(rows, labels, l2=5e-3, lr=0.1, epochs=150):
    if not rows:
        return None
    d = len(rows[0])
    mean = [sum(r[j] for r in rows) / len(rows) for j in range(d)]
    std = [math.sqrt(max(1e-9, sum((r[j] - mean[j]) ** 2 for r in rows) / len(rows))) for j in range(d)]
    std = [s if s > 1e-9 else 1.0 for s in std]
    w = [0.0] * d
    base = sum(labels) / len(labels)
    b = math.log(max(1e-6, base) / max(1e-6, 1 - base))
    for ep in range(epochs):
        for r, yv in zip(rows, labels):
            xs = [(r[j] - mean[j]) / std[j] for j in range(d)]
            p = 1.0 / (1.0 + math.exp(-max(-30, min(30, b + sum(w[j] * xs[j] for j in range(d))))))
            g = p - yv
            for j in range(d):
                w[j] -= lr * (g * xs[j] + l2 * w[j])
            b -= lr * g
    return {"w": w, "b": b, "mean": mean, "std": std}


def _logit_p(model, r):
    if model is None:
        return 0.5
    xs = [(r[j] - model["mean"][j]) / model["std"][j] for j in range(len(r))]
    z = model["b"] + sum(wj * xj for wj, xj in zip(model["w"], xs))
    return 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))


# --------------------------------------------------------------------------- sim
def _bar_state(bars, i, entry_i, direction, entry_px, sl, t1, atr_v, pr, cfg, mfe_R, held):
    """Feature dicts for the three heads, at bar i of an open trade."""
    b = bars[i]
    sgn = 1 if direction == "LONG" else -1
    adverse_move = (entry_px - b["c"]) * sgn                      # + => losing
    dist_sl = abs(b["c"] - sl) / atr_v
    dist_t1 = abs(t1 - b["c"]) / atr_v
    net = pr[i]["net"] * sgn                                      # + => favourable
    # slope/accel of net over last 3 bars
    seg = [pr[j]["net"] * sgn for j in range(max(entry_i, i - 3), i + 1)]
    slope = (seg[-1] - seg[0]) / max(1, len(seg) - 1) if len(seg) >= 2 else 0.0
    accel = (seg[-1] - 2 * seg[-2] + seg[-3]) if len(seg) >= 3 else 0.0
    g = pr[i]
    wick_rej = g["geo_lower_wick_ratio"] if direction == "LONG" else g["geo_upper_wick_ratio"]
    threat = {
        "dist_sl_atr": max(0.0, 2.0 - dist_sl),                  # closer to SL -> bigger
        "adverse_net": max(0.0, -net) / 50.0,
        "adverse_accel": max(0.0, -accel) / 20.0,
        "opt_deterioration": 0.0,                                # options too thin -> 0, flagged
        "oi_deterioration": 0.0,
        "mtf_deterioration": 1.0 if slope < -1.0 else 0.0,
        "bars_held_frac": held / cfg["tm_max_hold_bars"],
    }
    recov = {
        "opp_net_increase": max(0.0, net) / 50.0,
        "wick_rejection": wick_rej,
        "pressure_reversal": 1.0 if (slope > 1.0 and adverse_move > 0) else 0.0,
        "opt_confirmation": 0.0,
        "oi_reversal": 0.0,
        "align_1m_3m": 1.0 if slope > 0 else 0.0,
    }
    target = {
        "fav_net": max(0.0, net) / 50.0,
        "fav_persistence": max(0.0, slope) / 10.0,
        "mfe_atr": mfe_R,
        "dist_t1_atr": dist_t1,
        "bars_left_frac": max(0.0, (cfg["tm_max_hold_bars"] - held) / cfg["tm_max_hold_bars"]),
    }
    return ([threat[k] for k in _TM_FEATS_THREAT],
            [recov[k] for k in _TM_FEATS_RECOV],
            [target[k] for k in _TM_FEATS_TARGET], slope)


def _simulate_one(bars, pr, entry_i, direction, cfg, models=None):
    """Run all three exit policies for a single entry. Returns dict of policy->trade."""
    c = cfg
    a = atr(bars[max(0, entry_i - c["atr_window"]): entry_i + 1], c["atr_window"], c)
    if a is None or a <= c["eps"]:
        return None
    sgn = 1 if direction == "LONG" else -1
    entry_px = bars[entry_i]["c"]
    sl0 = entry_px - sgn * c["tm_sl_atr"] * a
    t1 = entry_px + sgn * c["tm_t1_atr"] * a
    risk = abs(entry_px - sl0)

    out = {}
    for policy in ("fixed_sl", "early_exit", "trailing_sl"):
        sl = sl0
        exit_px = exit_reason = None
        mfe_R = mae_R = 0.0
        recovered = False
        went_adverse = False
        fav_streak = 0
        for k in range(1, c["tm_max_hold_bars"] + 1):
            i = entry_i + k
            if i >= len(bars):
                exit_px, exit_reason = bars[-1]["c"], "END_OF_DATA"
                break
            b = bars[i]
            fav = (b["h"] - entry_px) * sgn if sgn > 0 else (entry_px - b["l"]) * sgn * -1
            fav_ex = max((b["h"] - entry_px) * sgn, (b["l"] - entry_px) * sgn)
            adv_ex = min((b["h"] - entry_px) * sgn, (b["l"] - entry_px) * sgn)
            mfe_R = max(mfe_R, fav_ex / risk)
            mae_R = min(mae_R, adv_ex / risk)
            if adv_ex < 0:
                went_adverse = True
            if went_adverse and (b["h"] >= entry_px >= b["l"]):
                recovered = True

            th, rc, tg, slope = _bar_state(bars, i, entry_i, direction, entry_px, sl, t1, a, pr,
                                           c, mfe_R, k)
            p_threat = _logit_p((models or {}).get("threat"), th)
            p_recov = _logit_p((models or {}).get("recovery"), rc)

            # ---- SL / target hit (intrabar, SL-first convention) ----
            hit_sl = (b["l"] <= sl) if sgn > 0 else (b["h"] >= sl)
            hit_t1 = (b["h"] >= t1) if sgn > 0 else (b["l"] <= t1)
            if hit_sl:
                exit_px, exit_reason = sl, "STOP"
                break
            if hit_t1:
                exit_px, exit_reason = t1, "TARGET"
                break

            # ---- policy-specific in-bar management ----
            if policy == "early_exit":
                if p_threat >= c["tm_threat_hi"] and p_recov < c["tm_recovery_hi"]:
                    exit_px, exit_reason = b["c"], "EARLY_EXIT_THREAT"
                    break
                if mfe_R >= c["tm_profit_lock_atr"] and slope < -1.0:
                    exit_px, exit_reason = b["c"], "EARLY_EXIT_MOMENTUM"
                    break
            if policy == "trailing_sl":
                fav_streak = fav_streak + 1 if slope > 0.5 else 0
                if fav_streak >= c["tm_trail_confirm_bars"]:
                    new_sl = b["c"] - sgn * c["tm_trail_atr"] * a
                    sl = max(sl, new_sl) if sgn > 0 else min(sl, new_sl)   # NEVER widen
        if exit_px is None:
            exit_px, exit_reason = bars[min(entry_i + c["tm_max_hold_bars"], len(bars) - 1)]["c"], "TIME"
        pts = (exit_px - entry_px) * sgn
        out[policy] = {
            "entry_i": entry_i, "direction": direction, "entry": round(entry_px, 4),
            "exit": round(exit_px, 4), "exit_reason": exit_reason,
            "points": round(pts, 4), "r_multiple": round(pts / risk, 4) if risk else None,
            "mae": round(mae_R, 3), "mfe": round(mfe_R, 3),
            "recovered_before_sl": bool(recovered and exit_reason != "STOP"),
            "went_adverse": went_adverse,
        }
    return out


def fit_tm_models(bars, pr, entries, cfg):
    """Fit the 3 heads on labelled in-trade bars from `entries` (TRAIN span only)."""
    Tr, Ty, Rr, Ry = [], [], [], []
    for (ei, direction) in entries:
        sim = _simulate_one(bars, pr, ei, direction, cfg, models=None)
        if not sim:
            continue
        fx = sim["fixed_sl"]
        stopped = fx["exit_reason"] == "STOP"
        a = atr(bars[max(0, ei - cfg["atr_window"]): ei + 1], cfg["atr_window"], cfg) or cfg["eps"]
        entry_px = bars[ei]["c"]
        sgn = 1 if direction == "LONG" else -1
        for k in range(1, min(cfg["tm_max_hold_bars"], len(bars) - ei - 1)):
            th, rc, tg, _ = _bar_state(bars, ei + k, ei, direction, entry_px,
                                       entry_px - sgn * cfg["tm_sl_atr"] * a,
                                       entry_px + sgn * cfg["tm_t1_atr"] * a, a, pr, cfg,
                                       fx["mfe"], k)
            Tr.append(th); Ty.append(1 if stopped else 0)
            if fx["went_adverse"]:
                Rr.append(rc); Ry.append(1 if fx["recovered_before_sl"] else 0)
    return {"threat": _logit_fit(Tr, Ty) if len(Tr) > 50 else None,
            "recovery": _logit_fit(Rr, Ry) if len(Rr) > 50 else None,
            "target": None}


def run_policies(bars, pr, entries, cfg, tm_models):
    """Simulate every entry under all 3 policies. Returns {policy: [trades]}."""
    res = {"fixed_sl": [], "early_exit": [], "trailing_sl": []}
    for (ei, direction) in entries:
        sim = _simulate_one(bars, pr, ei, direction, cfg, models=tm_models)
        if not sim:
            continue
        for pol, tr in sim.items():
            res[pol].append(tr)
    return res
