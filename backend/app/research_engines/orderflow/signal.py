"""
Questions 1 & 2 of the four:  WHICH DIRECTION?  and  HOW STRONG? (RK Score).

rk_score(row) -> {rk_score 0-100, direction LONG/SHORT/NONE, tilt, coverage,
components}. Additive, transparent, capability-aware: for the NIFTY cash index
the flow_* categories are PROXY (status != UNOBSERVABLE, so they DO count but
are honestly weaker); nothing here is UNOBSERVABLE for v1, so coverage = 1.0.
A category can still be N/A on a given bar (e.g. no value area yet) -> its
weight is redistributed for that bar.
"""
from __future__ import annotations

from .config import merged


def _clip01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _sig_component(row, c) -> dict:
    """Each entry: (signed_strength in [-1,1] or None, status). Sign convention:
    +1 = bullish evidence, -1 = bearish."""
    out: dict = {}

    # flow direction (PROXY): pressure net, smoothed a touch by cvd slope sign
    fp = row["flow_proxy"]
    out["flow_dir"] = (max(-1.0, min(1.0, fp)), "PROXY")

    # flow persistence (PROXY): normalised cvd slope
    sl = row["cvd_slope"]
    out["flow_persist"] = (max(-1.0, min(1.0, sl * 4.0)), "PROXY")

    # flow vs price (PROXY divergence): already -1/0/+1
    out["flow_vs_price"] = (float(row["flow_vs_price"]), "PROXY")

    # structure alignment (OK)
    st = row["structure_state"]
    smap = {"HH_HL": 0.8, "BOS_UP": 1.0, "CHOCH_UP": 0.6,
            "LH_LL": -0.8, "BOS_DN": -1.0, "CHOCH_DN": -0.6,
            "RANGE": 0.0, "MIXED": 0.0}
    out["structure"] = (smap.get(st, 0.0), "OK")

    # regime fit (OK): trending -> amplifies whatever direction structure says;
    # chop -> zero (kills the signal); ranging -> mild
    reg = row["regime_trend"]
    base_dir = smap.get(st, 0.0)
    rf = {"TRENDING": 1.0, "MIXED": 0.4, "RANGING": 0.2, "CHOP": 0.0}.get(reg, 0.3)
    out["regime_fit"] = ((base_dir and (1.0 if base_dir > 0 else -1.0)) * rf, "OK")

    # vwap context (PROXY): above vwap = bullish tilt, scaled, capped
    dev = row["vwap_dev_atr"]
    if dev is None:
        out["vwap_ctx"] = (None, "PROXY")
    else:
        out["vwap_ctx"] = (max(-1.0, min(1.0, dev / 2.0)), "PROXY")

    # value area (OK, TPO): close vs POC / inside-VA
    va = row["value_area"]
    if not va:
        out["value_area"] = (None, "OK")
    else:
        cl = row["close"]
        span = max(1e-9, va["vah"] - va["val"])
        out["value_area"] = (max(-1.0, min(1.0, (cl - va["poc"]) / span)), "OK")

    # reclaim / structural sweep proxy (OK)
    rc = row["reclaim"]
    if rc["fired"]:
        # a valid reclaim (dist_ratio in the Stage-7 sweet band) -> strong dir signal
        dr = rc["dist_ratio"] or 0.0
        strength = 1.0 if 0.28 < dr <= 0.60 else (0.4 if dr <= 0.28 else 0.2)
        out["reclaim"] = (rc["dir"] * strength, "OK")
    else:
        out["reclaim"] = (0.0, "OK")
    return out


def rk_score(row: dict, cfg: dict | None = None) -> dict:
    c = cfg or merged()
    w = c["rk_weights"]
    comp = _sig_component(row, c)

    num = 0.0        # signed weighted sum
    mag = 0.0        # weight of magnitude present
    avail = 0.0
    details = {}
    for k, weight in w.items():
        val, status = comp.get(k, (None, "N/A"))
        if val is None or status == "UNOBSERVABLE":
            details[k] = {"w": weight, "val": None, "status": status}
            continue
        avail += weight
        num += weight * val
        mag += weight * abs(val)
        details[k] = {"w": weight, "val": round(val, 3), "status": status}

    if avail <= 0:
        return {"rk_score": 0.0, "direction": "NONE", "tilt": 0.0,
                "coverage": 0.0, "components": details}

    tilt = num / avail                     # signed, [-1, 1]
    strength = mag / avail                 # unsigned magnitude of evidence
    # RK: 50 = neutral; push toward 0/100 by the *aligned* evidence
    rk = 50.0 + 50.0 * tilt
    rk = max(0.0, min(100.0, rk))
    # score for "how strong" = distance from 50 scaled by strength coherence
    strength_score = 100.0 * strength

    direction = "NONE"
    if abs(tilt) >= c["rk_dir_min_abs"] and strength_score >= c["rk_min"]:
        direction = "LONG" if tilt > 0 else "SHORT"

    return {
        "rk_score": round(strength_score, 1),   # 0-100 "how strong"
        "rk_tilt_score": round(rk, 1),          # 0-100 directional (50 neutral)
        "direction": direction,
        "tilt": round(tilt, 3),
        "coverage": round(avail / sum(w.values()), 3),
        "components": details,
    }


def detect(frame: list[dict], cfg: dict | None = None) -> list[dict]:
    """Per-bar signal rows. A 'signal' = direction != NONE AND rk_score >= rk_min
    AND it is a *fresh* flip (not the same direction as the previous signal bar)."""
    c = cfg or merged()
    out = []
    prev_dir = "NONE"
    for row in frame:
        if row["i"] < c["warmup_bars"]:
            continue
        r = rk_score(row, c)
        fresh = r["direction"] != "NONE" and r["direction"] != prev_dir
        if r["direction"] != "NONE":
            prev_dir = r["direction"]
        out.append({**r, "i": row["i"], "session_date": row["session_date"],
                    "hhmm": row["hhmm"], "close": row["close"], "fresh_signal": fresh})
    return out
