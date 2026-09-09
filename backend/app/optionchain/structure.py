"""
Layer 3 -- the Option Structure Engine.

`analyze(chain, *, realized_vol=None, cfg=None) -> OptionStructureState`

Consumes a canonical `OptionChain` + Layer-2 analytics and produces a
per-underlying STRUCTURE STATE -- NOT a trade signal, NOT an entry. It is a
deterministic, method-tagged, capability-aware read of where the option book
sits:

  * max-pain magnet   -- direction + distance + pull strength
  * PCR regime        -- OI and volume, banded
  * OI walls          -- nearest CE resistance / PE support + distance
  * IV skew bias      -- put-fear vs call-chase from the skew slope / 25Δ RR
  * IV vs realised    -- ATM IV rich / cheap / fair   (only if realised_vol given)
  * GEX regime        -- dealer net-gamma sign + flip / pin   (needs greeks+OI)

Degrades cleanly: no OI  -> max-pain / PCR / walls / GEX report UNKNOWN;
no greeks & no LTP -> skew / GEX report UNKNOWN.  Regime *labels* for GEX are
sign-tagged only (the pin/breakout hypothesis is still under test in
`engines/sr_engine`), never asserted as fact.

Thresholds live in `DEFAULT_CFG` and every one is overridable via `cfg=`.
No network, no DB, no order path.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from . import analytics as A
from .chain import OptionChain

DEFAULT_CFG = {
    # max-pain pull, by |distance| as % of spot
    "mp_at_pct": 0.15,          # within this -> "AT" (spot ~ pinned to max pain)
    "mp_strong_pct": 1.25,      # beyond this -> "STRONG" pull toward max pain
    # PCR(OI) regime bands  (puts / calls)
    "pcr_bull_oi": 1.30,        # >= -> put-heavy book -> support -> BULLISH lean
    "pcr_bear_oi": 0.70,        # <= -> call-heavy book -> resistance -> BEARISH lean
    "pcr_bull_vol": 1.30,
    "pcr_bear_vol": 0.70,
    # IV skew slope  d(pe_iv - ce_iv)/d(log-moneyness)
    "skew_put_fear": 0.15,      # slope <= -this  (puts bid up vs calls) -> PUT_FEAR
    "skew_call_chase": 0.15,    # slope >= +this                          -> CALL_CHASE
    "rr25_put_fear": 0.010,     # 25Δ RR >= this (put_iv - call_iv)       -> PUT_FEAR
    "rr25_call_chase": -0.010,
    # ATM IV vs realised vol
    "iv_rich_ratio": 1.15,
    "iv_cheap_ratio": 0.87,
    # walls "near" threshold, % of spot
    "wall_near_pct": 0.60,
    "atm_window": 10,
    "walls_k": 3,
}


def _band(x, lo, hi, low_lbl, mid_lbl, high_lbl):
    if x is None:
        return None
    return low_lbl if x <= lo else high_lbl if x >= hi else mid_lbl


@dataclass
class OptionStructureState:
    underlying: str
    expiry: str
    ts: str
    spot: float | None
    atm_strike: float | None
    source: str
    max_pain: dict = field(default_factory=dict)
    pcr_regime: dict = field(default_factory=dict)
    oi_walls: dict = field(default_factory=dict)
    iv_skew_bias: dict = field(default_factory=dict)
    iv_vs_realized: dict = field(default_factory=dict)
    gex_regime: dict = field(default_factory=dict)
    capability: dict = field(default_factory=dict)
    quality: dict | None = None
    notes: list = field(default_factory=list)
    summary: str = ""

    def to_dict(self):
        return {k: v for k, v in asdict(self).items()}


# --------------------------------------------------------------------------- #
def _max_pain_block(mp, spot, cfg, notes):
    if mp.status != "ok":
        notes.append(f"max_pain: {mp.status}")
        return {"status": mp.status, "magnet_dir": "UNKNOWN", "magnet_pull": "UNKNOWN"}
    dpct = mp.distance_pct
    if dpct is None:
        d = {"status": "no_spot", "max_pain_strike": mp.max_pain_strike,
             "magnet_dir": "UNKNOWN", "magnet_pull": "UNKNOWN"}
        return d
    adist = abs(dpct)
    direction = ("AT" if adist <= cfg["mp_at_pct"]
                 else "UP" if dpct > 0 else "DOWN")     # spot must move UP/DOWN to reach max pain
    pull = ("AT_MAGNET" if adist <= cfg["mp_at_pct"]
            else "STRONG" if adist >= cfg["mp_strong_pct"]
            else "MODERATE")
    return {"status": "ok", "max_pain_strike": mp.max_pain_strike, "spot": spot,
            "distance_pts": mp.distance_pts, "distance_pct": dpct,
            "magnet_dir": direction, "magnet_pull": pull, "method": mp.method}


def _pcr_block(p, cfg, notes):
    if p.status not in ("ok", "thin"):
        notes.append(f"pcr: {p.status}")
        return {"status": p.status, "regime": "UNKNOWN"}
    oi_lbl = _band(p.pcr_oi, cfg["pcr_bear_oi"], cfg["pcr_bull_oi"],
                   "BEARISH", "NEUTRAL", "BULLISH")
    vol_lbl = _band(p.pcr_vol, cfg["pcr_bear_vol"], cfg["pcr_bull_vol"],
                    "BEARISH", "NEUTRAL", "BULLISH")
    # combined: agree -> that; disagree or missing -> lean to OI, tag MIXED
    if oi_lbl and vol_lbl and oi_lbl == vol_lbl:
        regime = oi_lbl
    elif oi_lbl and vol_lbl and oi_lbl != vol_lbl:
        regime = f"MIXED({oi_lbl}_OI/{vol_lbl}_VOL)"
    else:
        regime = oi_lbl or vol_lbl or "UNKNOWN"
    return {"status": "ok", "pcr_oi": p.pcr_oi, "pcr_vol": p.pcr_vol,
            "pcr_oi_atm": p.pcr_oi_atm, "pcr_vol_atm": p.pcr_vol_atm,
            "oi_lean": oi_lbl, "vol_lean": vol_lbl, "regime": regime,
            "interpretation": "high PCR(OI) = put-heavy book = downside support (contrarian bullish)"}


def _walls_block(w, spot, cfg, notes):
    if w.status != "ok":
        notes.append(f"oi_walls: {w.status}")
        return {"status": w.status}
    near = cfg["wall_near_pct"]
    return {"status": "ok",
            "resistance": w.resistance, "support": w.support,
            "ce_wall": w.ce_wall, "pe_wall": w.pe_wall,
            "nearest_resistance": w.nearest_resistance,
            "nearest_support": w.nearest_support,
            "dist_res_pct": w.dist_res_pct, "dist_sup_pct": w.dist_sup_pct,
            "resistance_in_reach": bool(w.dist_res_pct is not None and abs(w.dist_res_pct) <= near),
            "support_in_reach": bool(w.dist_sup_pct is not None and abs(w.dist_sup_pct) <= near),
            "boxed": bool(w.dist_res_pct is not None and w.dist_sup_pct is not None
                          and abs(w.dist_res_pct) <= near and abs(w.dist_sup_pct) <= near)}


def _skew_block(s, cfg, notes):
    if s.status != "ok":
        notes.append(f"iv_skew: {s.status}")
        return {"status": s.status, "bias": "UNKNOWN"}
    slope, rr = s.skew_slope, s.rr_25
    bias, basis = "NEUTRAL", None
    if rr is not None:                       # 25Δ risk-reversal is the primary read
        basis = "rr_25"
        if rr >= cfg["rr25_put_fear"]:
            bias = "PUT_FEAR"
        elif rr <= cfg["rr25_call_chase"]:
            bias = "CALL_CHASE"
    elif slope is not None:                  # fall back to the robust skew slope
        basis = "skew_slope"
        if slope <= -cfg["skew_put_fear"]:
            bias = "PUT_FEAR"
        elif slope >= cfg["skew_call_chase"]:
            bias = "CALL_CHASE"
    return {"status": "ok", "atm_iv": s.atm_iv, "skew_slope": slope,
            "rr_25": rr, "fly_25": s.fly_25, "iv_source": s.iv_source,
            "bias": bias, "bias_basis": basis}


def _iv_rv_block(atm_iv, realized_vol, cfg, notes):
    if atm_iv is None or realized_vol is None:
        if realized_vol is None:
            notes.append("iv_vs_realized: no realized_vol supplied")
        return {"status": "unknown", "atm_iv": atm_iv, "realized_vol": realized_vol,
                "state": "UNKNOWN"}
    ratio = atm_iv / realized_vol if realized_vol else None
    state = _band(ratio, cfg["iv_cheap_ratio"], cfg["iv_rich_ratio"],
                  "IV_CHEAP", "FAIR", "IV_RICH") or "UNKNOWN"
    return {"status": "ok", "atm_iv": round(atm_iv, 5),
            "realized_vol": round(realized_vol, 5),
            "ratio": round(ratio, 4) if ratio else None, "state": state}


def _gex_block(g, spot, notes):
    if g.status != "ok":
        notes.append(f"gex: {g.status}")
        return {"status": g.status, "regime": "UNKNOWN"}
    regime = {1: "NET_LONG_GAMMA", -1: "NET_SHORT_GAMMA", 0: "GAMMA_BALANCED"}[g.regime_sign]
    return {"status": "ok", "total_shape": g.total_shape, "regime_sign": g.regime_sign,
            "regime": regime, "flip_strike": g.flip_strike, "pin_strike": g.pin_strike,
            "sigma": g.sigma, "sigma_src": g.sigma_src,
            "spot_vs_flip": (None if g.flip_strike is None or not spot
                             else "ABOVE" if spot > g.flip_strike else "BELOW"),
            "hypothesis": "sign only; long-gamma=>mean-revert / short-gamma=>trend is UNVALIDATED"}


def _summarize(st: OptionStructureState) -> str:
    bits = []
    mp = st.max_pain
    if mp.get("magnet_dir") not in (None, "UNKNOWN"):
        dpct = "" if mp.get("magnet_dir") == "AT" else f" {mp.get('distance_pct')}%"
        bits.append(f"max-pain {mp['max_pain_strike']:g} "
                    f"({mp['magnet_dir']}{dpct}, {mp['magnet_pull']})")
    if st.pcr_regime.get("regime") not in (None, "UNKNOWN"):
        bits.append(f"PCR {st.pcr_regime.get('pcr_oi')} -> {st.pcr_regime['regime']}")
    w = st.oi_walls
    if w.get("status") == "ok":
        boxed = " [BOXED]" if w.get("boxed") else ""
        bits.append(f"walls R={w.get('nearest_resistance')} "
                    f"S={w.get('nearest_support')}{boxed}")
    if st.iv_skew_bias.get("bias") not in (None, "UNKNOWN", "NEUTRAL"):
        bits.append(f"skew {st.iv_skew_bias['bias']}")
    if st.iv_vs_realized.get("state") not in (None, "UNKNOWN"):
        bits.append(f"ATM-IV {st.iv_vs_realized['state']}")
    if st.gex_regime.get("regime") not in (None, "UNKNOWN"):
        bits.append(f"GEX {st.gex_regime['regime']}"
                    + (f" flip@{st.gex_regime['flip_strike']:g}"
                       if st.gex_regime.get("flip_strike") else ""))
    return "; ".join(bits) if bits else "insufficient option data for a structure read"


def analyze(chain: OptionChain, *, realized_vol: float | None = None,
            cfg: dict | None = None) -> OptionStructureState:
    c = {**DEFAULT_CFG, **(cfg or {})}
    notes: list = []
    st = OptionStructureState(
        underlying=chain.underlying, expiry=chain.expiry, ts=chain.ts,
        spot=chain.spot, atm_strike=chain.atm_strike,
        source=chain.source, quality=chain.quality,
        capability=dict(chain.capability or {}))

    mp = A.max_pain(chain)
    p = A.pcr(chain, atm_window=c["atm_window"])
    sk = A.iv_skew(chain)
    w = A.oi_walls(chain, k=c["walls_k"])
    g = A.gex(chain)

    st.max_pain = _max_pain_block(mp, chain.spot, c, notes)
    st.pcr_regime = _pcr_block(p, c, notes)
    st.oi_walls = _walls_block(w, chain.spot, c, notes)
    st.iv_skew_bias = _skew_block(sk, c, notes)
    st.iv_vs_realized = _iv_rv_block(sk.atm_iv if sk.status == "ok" else None,
                                     realized_vol, c, notes)
    st.gex_regime = _gex_block(g, chain.spot, notes)

    ran = sum(1 for b in (st.max_pain, st.pcr_regime, st.oi_walls,
                          st.iv_skew_bias, st.gex_regime)
              if b.get("status") == "ok")
    st.capability["structure_blocks_ok"] = ran
    st.capability["structure_complete"] = ran >= 4
    st.notes = notes + [f"{ran}/5 structure blocks resolved",
                        f"cfg: mp_at={c['mp_at_pct']}% pcr_bull={c['pcr_bull_oi']} "
                        f"skew_thr={c['skew_put_fear']}"]
    st.summary = _summarize(st)
    return st
