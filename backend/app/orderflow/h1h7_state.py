"""
H1 / H7 Structural State Engine  --  READ-ONLY SHADOW / OBSERVATION MODE.

This is NOT a trading-signal generator. It has no BUY / SELL / CALL / PUT /
ENTRY / ORDER semantics and no auto-execution. It only *classifies* a
completed price bar into the deterministic market-structure states that the
Stage-3 -> Stage-8 order-flow RESEARCH established, and reports which research
confidence the classification carries. It never places an order, sends a
notification, emits a trading signal, or infers an option premium.

Frozen research rules reproduced here verbatim (see the Stage docs):
  * abnormal spike            -- range percentile >= 0.90 vs prior bars
                                 (scripts/orderflow_stage3_validation.spike_percentile)
  * broken level L            -- nearest displaced prior structural level
                                 (scripts/orderflow_event_anatomy._broken_level)
  * reclaim distance boundary -- max completed close back through L over
                                 T+1..T+3, / spike range; knee ~= 0.60
                                 (ORDERFLOW_STAGE7_BOUNDARY.md)
  * H1_CONT gate              -- Stage-7 REFINED: acc2 AND n1_agree AND
                                 disp_atr >= 1.0 AND available_R >= 1.0.
                                 body_fraction is DISPLAY-ONLY, never a gate
                                 (Stage-7 rejected body_frac as a discriminator).
  * per-symbol status         -- H7 boundary = SUPPORTED (AVOID only; fading H7
                                 is REJECTED, Stage-8). H1_CONT = RESEARCH-ONLY
                                 and PROMISING for CRUDEOIL only; NIFTY / NATGAS
                                 continuation is NOT_VALIDATED (observe only).
  * unobservable              -- true aggressor volume / trade delta / bid-ask
                                 imbalance / depth imbalance / absorption /
                                 footprint / iceberg liquidity: DATA NOT
                                 AVAILABLE (no tick / L2 feed -- see
                                 ORDERFLOW_STAGE9_L2_RESEARCH.md). Never estimated.

Nothing here is ever labelled PROVEN.
"""
from __future__ import annotations

import csv as _csv
import os
import statistics as _st
from pathlib import Path

# ---------------------------------------------------------------- frozen constants
SPIKE_PCTL = 0.90          # Stage-3/6 abnormal-spike range percentile
MIN_PRIOR_RANGES = 12      # spike_percentile needs >= 12 prior bar ranges
START_IDX = 8              # Stage-6 event loop start
FWD_WINDOW = 3             # acceptance / reclaim observation window (T+1..T+3)

BODY_BETA = 0.55           # Stage-6 "strong body" -- DISPLAY ONLY here, NOT a gate
DISP_ATR_GATE = 1.0        # Stage-7 REFINED H1_CONT gate (replaces body_frac)
AVAIL_R_GATE = 1.0         # Stage-7 available_R interaction gate
RECLAIM_KNEE = 0.60        # Stage-7 hard-trap knee (reclaim_dist / spike_range)
RECLAIM_SHALLOW = 0.28     # Stage-7 lower band: <= this = shallow poke = AMBIGUOUS

# H1_CONT continuation research status, per symbol. Anything not listed is
# NOT_VALIDATED and is downgraded to observe-only.
H1_CONT_PROMISING_SYMBOLS = {"CRUDEOIL"}

MODE = "SHADOW_OBSERVATION"

UNOBSERVABLE_ORDERFLOW = [
    "aggressor_volume", "trade_delta", "bid_ask_imbalance", "depth_imbalance",
    "absorption", "footprint_concentration", "iceberg_liquidity",
    "true_constituent_causation",
]
UNOBSERVABLE_NOTE = (
    "DATA NOT AVAILABLE -- no aggressor-classified tick or L2 depth feed exists "
    "in this environment (see backend/ORDERFLOW_STAGE9_L2_RESEARCH.md). "
    "These order-flow internals are never estimated, synthesised, or backfilled."
)

RESEARCH_STATUS_LEGEND = {
    "SUPPORTED": "Reproduced on train/val/OOS and the spent Stage-6 holdout. "
                 "H7 reclaim-distance boundary as an AVOIDANCE classifier only "
                 "-- fading H7 is REJECTED (Stage-8).",
    "RESEARCH_ONLY_PROMISING": "Small positive expectancy on the UNDERLYING for "
                 "CRUDEOIL across every split incl. holdout. Research-only. "
                 "Does NOT survive ATM option spread/theta. Never PROVEN.",
    "NOT_VALIDATED": "The H1 continuation gate matched, but this symbol's "
                 "continuation edge did not hold across splits (NIFTY unstable; "
                 "NATURALGAS sub-slippage). Observe only -- no action.",
    "NO_ESTABLISHED_EDGE": "No research edge attaches to this state. Informational.",
    "UNOBSERVABLE": UNOBSERVABLE_NOTE,
    "PROVEN": "Not used. Nothing in this research is PROVEN "
              "(no untouched multi-month holdout; correlated intraday events).",
}

# state -> action map (the ONLY actions this engine can ever emit)
_ACTION = {
    "NEUTRAL": "NO_ACTION",
    "SPIKE_NO_LEVEL": "NO_ACTION",
    "H7_TRAP": "AVOID",
    "H7_LEANING_TRAP": "AVOID",
    "AMBIGUOUS": "NO_ACTION",
    "H1_WEAK": "NO_ACTION",
    "H1_CONT": "CONTINUATION_CANDIDATE",       # CRUDEOIL only, research-only
    "H1_CONT_OBSERVE": "NO_ACTION",            # NIFTY / NATGAS
    "H1_CONT_BLOCKED": "NO_ACTION",            # gate matched but available_R fails
}


# ================================================================ pure helpers
# (verbatim from scripts/orderflow_* -- kept local so the engine is standalone
#  and its behaviour is pinned by this module's unit tests)

def _num(x):
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _clean(bars: list) -> list:
    """h>=l only; fill close with the bar midpoint; volume -> 0 when absent.
    Matches app/orderflow/smart_money._clean."""
    out = []
    for b in bars:
        h, l, o, c, v = (_num(b.get("h")), _num(b.get("l")), _num(b.get("o")),
                         _num(b.get("c")), _num(b.get("v")))
        if h is None or l is None or h < l:
            continue
        out.append({"bar_start": b.get("bar_start"), "o": o, "h": h, "l": l,
                    "c": c if c is not None else (h + l) / 2,
                    "v": v if (v is not None and v > 0) else 0.0})
    out.sort(key=lambda b: str(b.get("bar_start") or ""))
    return out


def _atr(clean, i, p=14):
    """Stage-6 _atr: running TR list, mean of the last p. Causal (bars[:i+1])."""
    trs = []
    pc = None
    for b in clean[:i + 1]:
        tr = b["h"] - b["l"]
        if pc is not None:
            tr = max(tr, abs(b["h"] - pc), abs(b["l"] - pc))
        trs.append(tr)
        pc = b["c"]
    if len(trs) < 2:
        return trs[-1] if trs else 0.0
    return _st.fmean(trs[-p:])


def _running_base(clean):
    """Stage-3 HSess.base[i] = median of prior bar ranges (bars[:i])."""
    base = [0.0] * len(clean)
    rs = []
    for i, b in enumerate(clean):
        base[i] = _st.median(rs) if rs else 0.0
        if b["h"] > b["l"]:
            rs.append(b["h"] - b["l"])
    return base


def _fractals(clean):
    """3-bar fractal low/high arrays. Index j is confirmed at j+1, so callers
    only read j <= idx-2 (that confirmation bar is then a completed bar)."""
    n = len(clean)
    flo = [False] * n
    fhi = [False] * n
    for i in range(1, n - 1):
        if clean[i]["l"] <= clean[i - 1]["l"] and clean[i]["l"] <= clean[i + 1]["l"]:
            flo[i] = True
        if clean[i]["h"] >= clean[i - 1]["h"] and clean[i]["h"] >= clean[i + 1]["h"]:
            fhi[i] = True
    return flo, fhi


def _dev_va(bars, value_pct=0.70, nbins=48):
    """Stage-3 _dev_va: volume-free TPO value area -> (VAL, POC, VAH)."""
    if len(bars) < 8:
        return None
    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)
    if hi <= lo:
        return None
    w = (hi - lo) / nbins
    cnt = [0.0] * nbins
    for b in bars:
        i0 = max(0, min(nbins - 1, int((b["l"] - lo) / w)))
        i1 = max(0, min(nbins - 1, int((b["h"] - lo) / w)))
        share = 1.0 / (i1 - i0 + 1)
        for i in range(i0, i1 + 1):
            cnt[i] += share
    poc_i = max(range(nbins), key=lambda i: cnt[i])
    total = sum(cnt)
    lo_i = hi_i = poc_i
    acc = cnt[poc_i]
    while acc < value_pct * total and (lo_i > 0 or hi_i < nbins - 1):
        left = cnt[lo_i - 1] if lo_i > 0 else -1
        right = cnt[hi_i + 1] if hi_i < nbins - 1 else -1
        if right >= left:
            hi_i += 1
            acc += cnt[hi_i]
        else:
            lo_i -= 1
            acc += cnt[lo_i]
    price = lambda i: lo + (i + 0.5) * w
    return (round(price(lo_i), 2), round(price(poc_i), 2), round(price(hi_i), 2))


def _va_series(clean):
    """Stage-3 HSess cadence: va[i] = _dev_va(clean[:i]) recomputed only when
    i >= 15 and i % 3 == 0, carried forward otherwise."""
    n = len(clean)
    va = [None] * n
    last = None
    for i in range(n):
        if i >= 15 and i % 3 == 0:
            last = _dev_va(clean[:i])
        va[i] = last
    return va


def spike_percentile(clean, idx):
    """Stage-3: fraction of prior completed bar ranges <= the bar-idx range."""
    r = clean[idx]["h"] - clean[idx]["l"]
    prior = [x["h"] - x["l"] for x in clean[:idx] if x["h"] > x["l"]]
    if len(prior) < MIN_PRIOR_RANGES:
        return None
    return sum(1 for x in prior if x <= r) / len(prior)


def _classify_spike(b):
    """scripts/orderflow_event_anatomy._classify_spike (verbatim)."""
    rng = b["h"] - b["l"]
    o, c = b.get("o"), b.get("c")
    if rng <= 0 or o is None or c is None:
        return "neutral", 0.0, 0.0, 0.0
    body = abs(c - o)
    uw = b["h"] - max(o, c)
    lw = min(o, c) - b["l"]
    body_pct = body / rng
    up = c > o
    if lw >= 1.0 * (body or 1e-9) and (c - b["l"]) / rng >= 0.6:
        cl = "bullish_rejection"
    elif uw >= 1.0 * (body or 1e-9) and (b["h"] - c) / rng >= 0.6:
        cl = "bearish_rejection"
    elif body_pct >= 0.55 and up:
        cl = "bullish_expansion"
    elif body_pct >= 0.55 and not up:
        cl = "bearish_expansion"
    else:
        cl = "neutral"
    return cl, round(body_pct, 3), round(uw, 2), round(lw, 2)


def _levels(clean, idx, va):
    """scripts/orderflow_event_anatomy._levels (verbatim). Fully causal:
    fractal search starts at idx-2 so its confirmation bar is <= idx-1."""
    fr_hi = next((clean[j]["h"] for j in range(idx - 2, 0, -1)
                  if _FRAC_HI[j]), None)
    fr_lo = next((clean[j]["l"] for j in range(idx - 2, 0, -1)
                  if _FRAC_LO[j]), None)
    sess_hi = max(x["h"] for x in clean[:idx]) if idx else None
    sess_lo = min(x["l"] for x in clean[:idx]) if idx else None
    prev_hi = clean[idx - 1]["h"] if idx else None
    prev_lo = clean[idx - 1]["l"] if idx else None
    return {
        "vah": va[2] if va else None, "poc": va[1] if va else None,
        "val": va[0] if va else None, "swing_hi": fr_hi, "swing_lo": fr_lo,
        "sess_hi": sess_hi, "sess_lo": sess_lo, "prev_hi": prev_hi, "prev_lo": prev_lo,
    }


# module-level fractal arrays, set per-session by classify_session (the research
# _levels closes over s.frac_hi / s.frac_lo the same way)
_FRAC_LO: list = []
_FRAC_HI: list = []


def _broken_level(clean, idx, direction, lv):
    """scripts/orderflow_continuation_trap._broken_level (verbatim)."""
    b = clean[idx]
    if direction == "LONG":
        refs = [lv[k] for k in ("prev_hi", "swing_hi", "vah", "sess_hi")
                if lv.get(k) is not None and lv[k] <= b["h"] + 1e-9]
        return (max(refs), "LONG") if refs else (None, "LONG")
    refs = [lv[k] for k in ("prev_lo", "swing_lo", "val", "sess_lo")
            if lv.get(k) is not None and lv[k] >= b["l"] - 1e-9]
    return (min(refs), "SHORT") if refs else (None, "SHORT")


def _broken_level_kind(lv, L, direction):
    keys = ("prev_hi", "swing_hi", "vah", "sess_hi") if direction == "LONG" \
        else ("prev_lo", "swing_lo", "val", "sess_lo")
    for k in keys:
        if lv.get(k) is not None and abs(lv[k] - L) < 1e-6:
            return {"prev_hi": "prior_bar_high", "prev_lo": "prior_bar_low",
                    "swing_hi": "swing_high", "swing_lo": "swing_low",
                    "vah": "value_area_high", "val": "value_area_low",
                    "sess_hi": "session_high", "sess_lo": "session_low"}[k]
    return "structural_level"


def _n1(clean, idx, direction, L):
    """scripts/orderflow_entry_timing._n1 (verbatim; only the `agree` field is used)."""
    c = clean
    if idx + 1 >= len(c):
        return None
    b, nb = c[idx], c[idx + 1]
    up = (nb.get("c") or 0) >= (nb.get("o") or 0)
    agree = (up and direction == "LONG") or ((not up) and direction == "SHORT")
    return {"agree": agree}


def _reclaimed_by(clean, idx, direction, L, w):
    """scripts/orderflow_continuation_trap._reclaimed_by (verbatim).
    First offset 1..w at which a completed candle CLOSES back through L."""
    if L is None:
        return None
    c = clean
    for k in range(1, w + 1):
        j = idx + k
        if j >= len(c):
            return None
        if (c[j]["c"] < L) if direction == "LONG" else (c[j]["c"] > L):
            return k
    return None


def _avail_R(lv, entry, R, direction):
    """scripts/orderflow_continuation_trap._avail_R (verbatim). Returns None when
    NO opposing structural level exists in the bar-derived set -- available_R is
    then UNOBSERVABLE and is never fabricated."""
    if not R:
        return None
    if direction == "LONG":
        opp = [lv[k] for k in ("vah", "swing_hi", "sess_hi") if lv.get(k) and lv[k] > entry]
    else:
        opp = [lv[k] for k in ("val", "swing_lo", "sess_lo") if lv.get(k) and lv[k] < entry]
    return round(abs(min(opp, key=lambda v: abs(v - entry)) - entry) / R, 2) if opp else None


# ================================================================ named deterministic API
def detect_abnormal_spike(clean, base, idx):
    """Stage-3/6 S1. Returns a dict (never None) with is_spike + the measured
    range percentile / range multiple / spike direction, or is_spike False."""
    if idx < START_IDX or idx >= len(clean) or base[idx] <= 0:
        return {"is_spike": False, "range_pctile": None, "range_x": None,
                "direction": None, "classification": None}
    b = clean[idx]
    rng = b["h"] - b["l"]
    o, c = b.get("o"), b.get("c")
    pct = spike_percentile(clean, idx)
    if pct is None or pct < SPIKE_PCTL or o is None or c is None or rng <= 0:
        return {"is_spike": False, "range_pctile": (round(pct, 4) if pct is not None else None),
                "range_x": (round(rng / base[idx], 3) if base[idx] else None),
                "direction": None, "classification": None}
    cls, _bp, _uw, _lw = _classify_spike(b)
    direction = ("LONG" if cls.startswith("bull") else "SHORT" if cls.startswith("bear")
                 else ("LONG" if c >= o else "SHORT"))
    return {"is_spike": True, "range_pctile": round(pct, 4),
            "range_x": round(rng / base[idx], 3),
            "direction": direction, "classification": cls}


def calculate_spike_range(b) -> float:
    """Stage-3 spike_range = bar high - bar low."""
    return float(b["h"] - b["l"])


def calculate_reclaim_distance(clean, idx, direction, L, spike_range):
    """Stage-7 reclaim distance. rd = max over T+1..T+3 of how far a COMPLETED
    close sits back through L; ratio = rd / spike_range. Zero-look-ahead: only
    reads bars idx+1 .. idx+3."""
    rd = 0.0
    for j in range(1, FWD_WINDOW + 1):
        if idx + j < len(clean):
            cj = clean[idx + j]["c"]
            back = (L - cj) if direction == "LONG" else (cj - L)
            rd = max(rd, back)
    ratio = (rd / spike_range) if spike_range > 0 else 0.0
    return {"reclaim_distance": round(rd, 4),
            "reclaim_distance_ratio": round(ratio, 4),
            "reclaimed_within_3": _reclaimed_by(clean, idx, direction, L, FWD_WINDOW) is not None}


def classify_h7_trap(reclaim_ratio, reclaimed_within_3) -> str:
    """Stage-7 reclaim-distance bands. AVOID classifier only -- NOT a fade
    trigger (fading H7 is REJECTED, Stage-8)."""
    if not reclaimed_within_3:
        return "NONE"
    if reclaim_ratio > RECLAIM_KNEE:
        return "H7_TRAP"
    if reclaim_ratio > RECLAIM_SHALLOW:
        return "H7_LEANING_TRAP"
    return "AMBIGUOUS_SHALLOW"


def detect_n1_agreement(clean, idx, direction, L) -> bool:
    """Stage-6 n1_agree: reaction candle (idx+1) close-vs-open agrees with the
    spike direction."""
    n1 = _n1(clean, idx, direction, L)
    return bool(n1 and n1["agree"])


def _acceptance(clean, idx, direction, L, dist_L):
    """Stage-6 acc[k]: k completed closes stay beyond L AND the spike bar itself
    closed beyond L (dist_L > 0)."""
    acc = {}
    for k in (1, 2, 3):
        held = True
        for j in range(1, k + 1):
            if idx + j >= len(clean):
                held = False
                break
            cj = clean[idx + j]["c"]
            if (cj <= L) if direction == "LONG" else (cj >= L):
                held = False
                break
        acc[k] = bool(held and dist_L > 0)
    return acc


def calculate_available_R(lv, entry, R_pts, direction):
    """Stage-6/7 available_R at the reference geometry (entry = spike close,
    stop = spike extreme +/- 3% pad, so R_pts = |entry - stop|). Returns None
    when there is no opposing structural level -- NEVER fabricated."""
    return _avail_R(lv, entry, R_pts, direction)


def classify_h1_continuation(acc2, n1_agree, disp_atr, available_R) -> str:
    """Stage-7 REFINED H1 continuation gate.

    Gate = acc2 AND n1_agree AND disp_atr >= 1.0 AND available_R >= 1.0.

    body_fraction is NOT part of this gate (Stage-7 found body_frac is not a
    strong discriminator; disp_atr is the refined form). Returns one of:
      H1_CONT           -- full gate satisfied
      H1_CONT_BLOCKED   -- acc2 & n1 & disp_atr satisfied but available_R is
                           None (unobservable) or < 1.0
      NO_H1             -- the structural gate did not match
    """
    structural = bool(acc2 and n1_agree and disp_atr is not None
                      and disp_atr >= DISP_ATR_GATE)
    if not structural:
        return "NO_H1"
    if available_R is None or available_R < AVAIL_R_GATE:
        return "H1_CONT_BLOCKED"
    return "H1_CONT"


# ================================================================ state machine
def classify_market_state(ctx: dict) -> dict:
    """Pure deterministic state machine over a single completed-bar context.

    `ctx` keys (all already computed causally by classify_session, or supplied
    directly by a unit test):
      symbol, timestamp, is_spike, direction, spike_range, broken_level,
      broken_level_kind, range_pctile, range_x, atr, disp_atr, body_fraction,
      acc1, acc2, n1_agreement, reclaimed_within_3, reclaim_distance,
      reclaim_distance_ratio, available_R

    Returns the shadow event dict. Emits ONLY the actions AVOID / NO_ACTION /
    CONTINUATION_CANDIDATE. Never BUY / SELL / ORDER / a premium.
    """
    sym = str(ctx.get("symbol") or "").upper()
    out = {
        "symbol": sym,
        "timestamp": ctx.get("timestamp"),
        "mode": MODE,
        "live_trading": False,
        "spike_direction": ctx.get("direction"),
        "spike_range": ctx.get("spike_range"),
        "range_pctile": ctx.get("range_pctile"),
        "range_x": ctx.get("range_x"),
        "atr": ctx.get("atr"),
        "broken_level": ctx.get("broken_level"),
        "broken_level_kind": ctx.get("broken_level_kind"),
        "reclaim_distance": ctx.get("reclaim_distance"),
        "reclaim_distance_ratio": ctx.get("reclaim_distance_ratio"),
        "reclaimed_within_3": ctx.get("reclaimed_within_3"),
        "available_R": ctx.get("available_R"),
        "n1_agreement": ctx.get("n1_agreement"),
        "acc1": ctx.get("acc1"),
        "acc2": ctx.get("acc2"),
        "disp_atr": ctx.get("disp_atr"),
        # display-only, explicitly NOT a gate (Stage-7)
        "body_fraction": ctx.get("body_fraction"),
        "body_fraction_note": "informational only -- NOT a classifier gate (Stage-7)",
        "unobservable_orderflow": list(UNOBSERVABLE_ORDERFLOW),
        "unobservable_note": UNOBSERVABLE_NOTE,
    }

    if not ctx.get("is_spike"):
        out.update(state="NEUTRAL", action="NO_ACTION",
                   research_status="NO_ESTABLISHED_EDGE",
                   reason="range percentile below the 0.90 abnormal-spike threshold")
        return out

    L = ctx.get("broken_level")
    if L is None:
        out.update(state="SPIKE_NO_LEVEL", action="NO_ACTION",
                   research_status="NO_ESTABLISHED_EDGE",
                   reason=("abnormal %s spike but it displaced no prior structural "
                           "level -- nothing to classify" % (ctx.get("direction") or "?").lower()))
        return out

    ratio = ctx.get("reclaim_distance_ratio") or 0.0
    rw3 = bool(ctx.get("reclaimed_within_3"))
    h7 = classify_h7_trap(ratio, rw3)
    knd = ctx.get("broken_level_kind") or "structural_level"
    dirl = (ctx.get("direction") or "?").lower()

    if h7 == "H7_TRAP":
        out.update(
            state="H7_TRAP", action="AVOID", research_status="SUPPORTED",
            reason=("abnormal %s spike broke %s %.2f; a completed close returned "
                    "%.2f pts back through L = %.2fx spike_range (> %.2f knee) -> hard trap. "
                    "AVOID -- do not buy the break. Fading H7 is REJECTED (Stage-8)."
                    % (dirl, knd, L, ctx.get("reclaim_distance") or 0.0, ratio, RECLAIM_KNEE)))
        return out
    if h7 == "H7_LEANING_TRAP":
        out.update(
            state="H7_LEANING_TRAP", action="AVOID", research_status="SUPPORTED",
            reason=("abnormal %s spike broke %s %.2f; a completed close returned "
                    "%.2fx spike_range back through L (%.2f < ratio <= %.2f) -> leaning trap. "
                    "AVOID (conservative)." % (dirl, knd, L, ratio, RECLAIM_SHALLOW, RECLAIM_KNEE)))
        return out
    if h7 == "AMBIGUOUS_SHALLOW":
        out.update(
            state="AMBIGUOUS", action="NO_ACTION", research_status="NO_ESTABLISHED_EDGE",
            reason=("abnormal %s spike broke %s %.2f; a completed close ticked back "
                    "through L by only %.2fx spike_range (<= %.2f) -- shallow poke, "
                    "genuinely ambiguous, NOT a trap. No action."
                    % (dirl, knd, L, ratio, RECLAIM_SHALLOW)))
        return out

    # --- not reclaimed within T+1..T+3 -> H1 branch --------------------------
    h1 = classify_h1_continuation(ctx.get("acc2"), ctx.get("n1_agreement"),
                                  ctx.get("disp_atr"), ctx.get("available_R"))
    if h1 == "H1_CONT":
        if sym in H1_CONT_PROMISING_SYMBOLS:
            out.update(
                state="H1_CONT", action="CONTINUATION_CANDIDATE",
                research_status="RESEARCH_ONLY_PROMISING",
                reason=("abnormal %s spike broke %s %.2f; 2 completed closes held "
                        "beyond L; reaction candle agrees; disp_atr %.2f >= %.2f; "
                        "available_R %.2f >= %.2f; no reclaim -> continuation candidate. "
                        "RESEARCH-ONLY / PROMISING for %s (small edge, not PROVEN, "
                        "does not survive option spread/theta)."
                        % (dirl, knd, L, ctx.get("disp_atr") or 0.0, DISP_ATR_GATE,
                           ctx.get("available_R") or 0.0, AVAIL_R_GATE, sym)))
        else:
            out.update(
                state="H1_CONT_OBSERVE", action="NO_ACTION",
                research_status="NOT_VALIDATED",
                reason=("abnormal %s spike broke %s %.2f; H1 continuation gate matched "
                        "(acc2 + n1_agree + disp_atr %.2f + available_R %.2f) BUT %s "
                        "continuation is NOT_VALIDATED across research splits -- observe only, "
                        "no action." % (dirl, knd, L, ctx.get("disp_atr") or 0.0,
                                        ctx.get("available_R") or 0.0, sym)))
        return out
    if h1 == "H1_CONT_BLOCKED":
        aR = ctx.get("available_R")
        if aR is None:
            why = ("available_R is UNOBSERVABLE -- no opposing structural level in the "
                   "bar-derived level set; it is NOT fabricated")
        else:
            why = "available_R %.2f < %.2f -- insufficient structural room" % (aR, AVAIL_R_GATE)
        out.update(
            state="H1_CONT_BLOCKED", action="NO_ACTION",
            research_status="NO_ESTABLISHED_EDGE",
            reason=("abnormal %s spike broke %s %.2f; acceptance + n1_agree + disp_atr gate "
                    "matched but %s. No action." % (dirl, knd, L, why)))
        return out

    # --- structural gate did not match -----------------------------------
    if ctx.get("acc1") and ctx.get("n1_agreement"):
        out.update(
            state="H1_WEAK", action="NO_ACTION", research_status="NO_ESTABLISHED_EDGE",
            reason=("abnormal %s spike broke %s %.2f; 1 close beyond L + reaction agrees "
                    "but the refined H1 gate (acc2 + disp_atr>=%.1f + available_R>=%.1f) "
                    "did not match. No action." % (dirl, knd, L, DISP_ATR_GATE, AVAIL_R_GATE)))
        return out

    out.update(
        state="AMBIGUOUS", action="NO_ACTION", research_status="NO_ESTABLISHED_EDGE",
        reason=("abnormal %s spike broke %s %.2f; neither the hard-trap boundary nor the "
                "H1 acceptance gate is met. Ambiguous -- no action." % (dirl, knd, L)))
    return out


# ================================================================ session driver
def classify_session(bars: list, symbol: str, *, only_last: bool = False) -> dict:
    """Classify every completed ELIGIBLE bar of one session (a bar is eligible
    once bars T+1..T+3 are all present -- completed candles only, zero
    look-ahead). Read-only. Returns {symbol, mode, live_trading, events, summary}.

    `only_last=True` returns just the most recent eligible spike event (the
    'latest completed bar' dashboard view)."""
    global _FRAC_LO, _FRAC_HI
    sym = str(symbol or "").upper()
    clean = _clean(bars)
    n = len(clean)
    base = _running_base(clean)
    _FRAC_LO, _FRAC_HI = _fractals(clean)
    va = _va_series(clean)

    events: list = []
    # eligible idx: needs >= START_IDX prior bars AND idx+3 completed (<= n-1)
    for idx in range(START_IDX, n - FWD_WINDOW):
        sp = detect_abnormal_spike(clean, base, idx)
        if not sp["is_spike"]:
            continue
        b = clean[idx]
        direction = sp["direction"]
        rng = calculate_spike_range(b)
        atr = _atr(clean, idx) or base[idx]
        disp_atr = (abs(b["c"] - b["o"]) / atr) if atr else None
        body_fraction = round(abs(b["c"] - b["o"]) / rng, 4) if rng > 0 else None

        lv = _levels(clean, idx, va[idx])
        L, _ = _broken_level(clean, idx, direction, lv)
        ctx = {
            "symbol": sym, "timestamp": b["bar_start"], "is_spike": True,
            "direction": direction, "spike_range": round(rng, 4),
            "range_pctile": sp["range_pctile"], "range_x": sp["range_x"],
            "atr": round(atr, 4) if atr else None,
            "disp_atr": round(disp_atr, 4) if disp_atr is not None else None,
            "body_fraction": body_fraction,
            "broken_level": (round(L, 4) if L is not None else None),
            "broken_level_kind": (_broken_level_kind(lv, L, direction) if L is not None else None),
        }
        if L is not None:
            dist_L = (b["c"] - L) if direction == "LONG" else (L - b["c"])
            acc = _acceptance(clean, idx, direction, L, dist_L)
            rc = calculate_reclaim_distance(clean, idx, direction, L, rng)
            # reference geometry: entry = spike close, stop = spike extreme -/+ 3% pad
            pad = 0.03 * rng
            spike_ext = b["l"] if direction == "LONG" else b["h"]
            stop = (spike_ext - pad) if direction == "LONG" else (spike_ext + pad)
            R_pts = abs(b["c"] - stop)
            ctx.update(
                acc1=acc[1], acc2=acc[2],
                n1_agreement=detect_n1_agreement(clean, idx, direction, L),
                reclaim_distance=rc["reclaim_distance"],
                reclaim_distance_ratio=rc["reclaim_distance_ratio"],
                reclaimed_within_3=rc["reclaimed_within_3"],
                available_R=calculate_available_R(lv, b["c"], R_pts, direction),
            )
        events.append(classify_market_state(ctx))

    if only_last:
        events = events[-1:] if events else []

    summary: dict = {}
    for e in events:
        summary[e["state"]] = summary.get(e["state"], 0) + 1

    return {
        "symbol": sym, "mode": MODE, "live_trading": False,
        "eligible_bars": max(0, n - FWD_WINDOW - START_IDX),
        "events": events, "summary": summary,
        "research_status_legend": RESEARCH_STATUS_LEGEND,
    }


# ================================================================ shadow CSV (append-only)
_CSV_COLUMNS = [
    "symbol", "timestamp", "mode", "live_trading", "state", "action",
    "research_status", "spike_direction", "spike_range", "range_pctile",
    "range_x", "atr", "disp_atr", "body_fraction", "broken_level",
    "broken_level_kind", "reclaim_distance", "reclaim_distance_ratio",
    "reclaimed_within_3", "available_R", "n1_agreement", "acc1", "acc2",
    "reason", "unobservable_orderflow",
]


def shadow_log_path() -> Path:
    override = os.environ.get("ORDERFLOW_H1H7_SHADOW_CSV")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data" / "orderflow_h1h7_shadow.csv"


def _flatten(ev: dict) -> dict:
    row = {k: ev.get(k) for k in _CSV_COLUMNS}
    row["unobservable_orderflow"] = "|".join(ev.get("unobservable_orderflow") or [])
    return row


def append_shadow_rows(events: list, path=None) -> dict:
    """Append event rows to the append-only shadow CSV. Dedups on
    (symbol, timestamp, state) against what is already on disk so re-running the
    read endpoint never bloats the log. No DB, no schema change, no order,
    no notification."""
    p = Path(path) if path else shadow_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    exists = p.exists()
    if exists:
        try:
            with p.open(newline="") as f:
                for r in _csv.DictReader(f):
                    seen.add((r.get("symbol"), r.get("timestamp"), r.get("state")))
        except Exception:
            seen = set()
    fresh = [e for e in events
             if (e.get("symbol"), e.get("timestamp"), e.get("state")) not in seen]
    if not fresh:
        return {"appended": 0, "path": str(p), "total_seen": len(seen)}
    with p.open("a", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        for e in fresh:
            w.writerow(_flatten(e))
    return {"appended": len(fresh), "path": str(p), "total_seen": len(seen) + len(fresh)}
