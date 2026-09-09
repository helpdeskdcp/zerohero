"""
Layer 5 -- Signal Qualification.

`qualify(state, *, external_signal=None, ann_p_win=None, cfg=None) -> Qualification`

Combines four INDEPENDENT inputs and returns `QUALIFIED | WATCH | NO_TRADE`:

  1. OptionStructureState        (Layer 3)   -- option-book context + a derived bias
  2. an existing directional signal for the underlying (RK / HCS)  -- the trigger
  3. ANN p_win                                (Layer 4, optional) -- confirmation
  4. the chain DQ score                       (Layer 1)           -- data trust

Each is a SEPARATE GATE. There is NO blended score: a single FAIL -> NO_TRADE;
every gate PASS/NA -> QUALIFIED; anything soft in between -> WATCH. This mirrors
the tri_compare hybrid discipline and SIGNAL != ENTRY -- structure alone is
never a trade, only context; it needs an external trigger to reach QUALIFIED.

Research-only. No network, no DB, no order path, no live-app import.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .structure import OptionStructureState

PASS, WATCH, FAIL, NA = "PASS", "WATCH", "FAIL", "NA"

DEFAULT_CFG = {
    # structure-bias vote weights  (max-pain magnet, PCR regime, IV-skew bias)
    "w_maxpain": 1.0,
    "w_pcr": 1.0,
    "w_skew": 0.5,
    "bias_thr": 1.0,             # |Σ w·vote| >= this -> LONG / SHORT, else NEUTRAL
    # structure completeness
    "min_blocks_watch": 3,      # < this -> G_STRUCTURE FAIL
    # ANN
    "require_ann": False,        # True -> a missing p_win caps the verdict at WATCH
    "ann_min": 0.55,            # >= -> PASS
    "ann_watch": 0.50,         # [ann_watch, ann_min) -> WATCH ; < -> FAIL
    # location (walls) gate
    "wall_room_min_pct": 0.35,  # aligned wall closer than this (and it's THE wall) -> FAIL
    # regime (GEX pin) soft gate
    "use_gex_context": True,
    "pin_band_pct": 0.20,       # spot within this % of pin_strike + long-gamma -> WATCH
    # DQ
    "dqs_hard_floor": 45.0,     # dqs below this -> FAIL even if verdict missing
}

_LONG, _SHORT, _NEUTRAL = "LONG", "SHORT", "NEUTRAL"


# --------------------------------------------------------------------------- #
#  external signal normaliser                                                  #
# --------------------------------------------------------------------------- #
def normalize_signal(sig) -> dict | None:
    """Accept RK / HCS / plain dicts. -> {direction, prob, confidence, score, source}."""
    if not sig:
        return None
    if not isinstance(sig, dict):
        return None
    d = (sig.get("direction") or sig.get("side") or sig.get("bias") or "").upper()
    if d in ("BUY", "LONG", "BULL", "BULLISH", "UP"):
        d = _LONG
    elif d in ("SELL", "SHORT", "BEAR", "BEARISH", "DOWN"):
        d = _SHORT
    elif d in ("FLAT", "NONE", "NEUTRAL", ""):
        d = "FLAT"
    prob = sig.get("prob")
    if prob is None:
        prob = sig.get("calibrated_probability", sig.get("probability", sig.get("p_win")))
    return {
        "direction": d,
        "prob": _f(prob),
        "confidence": _f(sig.get("confidence")),
        "score": _f(sig.get("score", sig.get("hcs_score", sig.get("rk_score")))),
        "source": str(sig.get("source") or sig.get("engine") or "external"),
    }


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
#  structure -> directional bias  (transparent vote, no black box)             #
# --------------------------------------------------------------------------- #
def structure_direction(state: OptionStructureState, cfg: dict | None = None) -> dict:
    c = {**DEFAULT_CFG, **(cfg or {})}
    votes = []

    mp = state.max_pain or {}
    if mp.get("status") == "ok" and mp.get("magnet_pull") in ("MODERATE", "STRONG"):
        v = {"UP": 1, "DOWN": -1}.get(mp.get("magnet_dir"), 0)
        if v:
            votes.append(("max_pain_magnet", v, c["w_maxpain"],
                          f"{mp.get('magnet_dir')} {mp.get('distance_pct')}% ({mp.get('magnet_pull')})"))

    pr = state.pcr_regime or {}
    if pr.get("status") == "ok":
        reg = pr.get("regime", "")
        v = 1 if reg == "BULLISH" else -1 if reg == "BEARISH" else 0
        if v:
            votes.append(("pcr_regime", v, c["w_pcr"], f"{reg} (PCR-OI {pr.get('pcr_oi')})"))

    sk = state.iv_skew_bias or {}
    if sk.get("status") == "ok":
        v = {"CALL_CHASE": 1, "PUT_FEAR": -1}.get(sk.get("bias"), 0)
        if v:
            votes.append(("iv_skew_bias", v, c["w_skew"],
                          f"{sk.get('bias')} (rr25 {sk.get('rr_25')})"))

    net = round(sum(v * w for _n, v, w, _d in votes), 3)
    bias = (_LONG if net >= c["bias_thr"]
            else _SHORT if net <= -c["bias_thr"]
            else _NEUTRAL)
    return {
        "bias": bias, "net": net, "threshold": c["bias_thr"],
        "votes": [{"cue": n, "vote": v, "weight": w, "detail": d}
                  for n, v, w, d in votes],
    }


# --------------------------------------------------------------------------- #
@dataclass
class Gate:
    name: str
    status: str
    detail: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class Qualification:
    underlying: str
    expiry: str
    ts: str
    spot: float | None
    verdict: str                      # QUALIFIED | WATCH | NO_TRADE
    direction: str                    # LONG | SHORT | NEUTRAL  (candidate / qualified)
    structure_bias: dict = field(default_factory=dict)
    external_signal: dict | None = None
    ann_p_win: float | None = None
    dq: dict = field(default_factory=dict)
    regime_context: str = "UNKNOWN"   # RANGE | TREND | NEUTRAL | UNKNOWN  (advisory)
    gates: list = field(default_factory=list)
    blocking: list = field(default_factory=list)
    watch_reasons: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    summary: str = ""

    def to_dict(self):
        d = asdict(self)
        d["gates"] = [g if isinstance(g, dict) else g.to_dict() for g in self.gates]
        return d


# --------------------------------------------------------------------------- #
def _g_dq(state, c, notes):
    q = state.quality or {}
    v = str(q.get("verdict") or "").upper()
    dqs = _f(q.get("dqs"))
    if v == "FAIL" or (dqs is not None and dqs < c["dqs_hard_floor"]):
        return Gate("G_DQ", FAIL, f"data quality {v or dqs} below the trade floor")
    if v == "WARN" or (v == "" and dqs is not None and dqs < 80):
        return Gate("G_DQ", WATCH, f"data quality {v or dqs} -- degraded, monitor only")
    if v == "PASS":
        return Gate("G_DQ", PASS, f"dqs {dqs}")
    return Gate("G_DQ", WATCH, "no DQ score on the chain")


def _g_structure(state, c):
    cap = state.capability or {}
    nb = cap.get("structure_blocks_ok", 0)
    if cap.get("structure_complete"):
        return Gate("G_STRUCTURE", PASS, f"{nb}/5 blocks")
    if nb >= c["min_blocks_watch"]:
        return Gate("G_STRUCTURE", WATCH, f"only {nb}/5 blocks resolved")
    return Gate("G_STRUCTURE", FAIL, f"only {nb}/5 blocks -- too little of the book is readable")


def _g_direction(sbias, ext, c):
    b = sbias["bias"]
    if ext and ext["direction"] in (_LONG, _SHORT):
        if b == _NEUTRAL:
            return Gate("G_DIRECTION", WATCH,
                        f"structure NEUTRAL -- does not confirm the {ext['direction']} signal"), ext["direction"]
        if b == ext["direction"]:
            return Gate("G_DIRECTION", PASS,
                        f"structure {b} agrees with the {ext['source']} signal"), b
        return Gate("G_DIRECTION", FAIL,
                    f"structure {b} conflicts with the {ext['direction']} signal"), ext["direction"]
    # no usable external trigger
    if ext and ext["direction"] == "FLAT":
        return Gate("G_DIRECTION", WATCH, "external signal is FLAT -- no trigger"), b
    if b == _NEUTRAL:
        return Gate("G_DIRECTION", WATCH, "structure-only and NEUTRAL -- nothing to qualify"), _NEUTRAL
    return Gate("G_DIRECTION", WATCH,
                f"structure-only {b} context -- needs an entry trigger (SIGNAL != ENTRY)"), b


def _g_ann(ann_p_win, c):
    if ann_p_win is None:
        return Gate("G_ANN", WATCH if c["require_ann"] else NA,
                    "no ANN p_win supplied")
    if ann_p_win >= c["ann_min"]:
        return Gate("G_ANN", PASS, f"p_win {ann_p_win:.3f}")
    if ann_p_win >= c["ann_watch"]:
        return Gate("G_ANN", WATCH, f"p_win {ann_p_win:.3f} in the grey band")
    return Gate("G_ANN", FAIL, f"p_win {ann_p_win:.3f} below {c['ann_watch']}")


def _g_location(state, direction, c):
    w = state.oi_walls or {}
    if w.get("status") != "ok":
        return Gate("G_LOCATION", NA, "no OI-wall read")
    if w.get("boxed"):
        return Gate("G_LOCATION", WATCH, "price boxed between the CE and PE walls -- poor directional location")
    if direction == _LONG and w.get("resistance_in_reach"):
        dr = w.get("dist_res_pct")
        if (w.get("nearest_resistance") == w.get("ce_wall")
                and dr is not None and abs(dr) < c["wall_room_min_pct"]):
            return Gate("G_LOCATION", FAIL,
                        f"long straight into the CE wall {w.get('ce_wall')} ({dr}% away)")
        return Gate("G_LOCATION", WATCH, f"CE resistance {w.get('nearest_resistance')} in reach ({dr}%)")
    if direction == _SHORT and w.get("support_in_reach"):
        ds = w.get("dist_sup_pct")
        if (w.get("nearest_support") == w.get("pe_wall")
                and ds is not None and abs(ds) < c["wall_room_min_pct"]):
            return Gate("G_LOCATION", FAIL,
                        f"short straight into the PE wall {w.get('pe_wall')} ({ds}% away)")
        return Gate("G_LOCATION", WATCH, f"PE support {w.get('nearest_support')} in reach ({ds}%)")
    return Gate("G_LOCATION", PASS, "room to the aligned wall")


def _regime_context(state, c):
    g = state.gex_regime or {}
    ivr = (state.iv_vs_realized or {}).get("state")
    if g.get("status") != "ok":
        base = "UNKNOWN"
    elif g.get("regime") == "NET_LONG_GAMMA":
        base = "RANGE"
    elif g.get("regime") == "NET_SHORT_GAMMA":
        base = "TREND"
    else:
        base = "NEUTRAL"
    # IV vs realised is a soft tie-breaker only
    if base == "NEUTRAL" and ivr == "IV_CHEAP":
        base = "TREND"
    elif base == "NEUTRAL" and ivr == "IV_RICH":
        base = "RANGE"
    return base


def _g_regime(state, direction, ctx, c):
    if not c["use_gex_context"]:
        return Gate("G_REGIME", NA, "gex context disabled")
    g = state.gex_regime or {}
    mp = state.max_pain or {}
    if g.get("status") != "ok":
        return Gate("G_REGIME", NA, "no GEX read")
    if direction in (_LONG, _SHORT) and ctx == "RANGE":
        near_pin = False
        pin = g.get("pin_strike")
        if pin and state.spot:
            near_pin = abs(state.spot - pin) / state.spot * 100.0 <= c["pin_band_pct"]
        if near_pin or mp.get("magnet_dir") == "AT":
            return Gate("G_REGIME", WATCH,
                        "long-gamma / near the pin -- chop risk for a directional trade "
                        "(GEX regime hypothesis is UNVALIDATED)")
    return Gate("G_REGIME", PASS, f"regime context {ctx}")


# --------------------------------------------------------------------------- #
def qualify(state: OptionStructureState, *, external_signal=None,
           ann_p_win: float | None = None, cfg: dict | None = None) -> Qualification:
    c = {**DEFAULT_CFG, **(cfg or {})}
    notes: list = []
    ext = normalize_signal(external_signal)
    ann_p_win = _f(ann_p_win)

    sbias = structure_direction(state, c)
    ctx = _regime_context(state, c)

    g_dq = _g_dq(state, c, notes)
    g_st = _g_structure(state, c)
    g_dir, direction = _g_direction(sbias, ext, c)
    g_ann = _g_ann(ann_p_win, c)
    g_loc = _g_location(state, direction, c)
    g_reg = _g_regime(state, direction, ctx, c)
    gates = [g_dq, g_st, g_dir, g_ann, g_loc, g_reg]

    blocking = [g.name for g in gates if g.status == FAIL]
    watch = [f"{g.name}: {g.detail}" for g in gates if g.status == WATCH]
    if blocking:
        verdict = "NO_TRADE"
    elif watch:
        verdict = "WATCH"
    else:
        verdict = "QUALIFIED"

    q = state.quality or {}
    out = Qualification(
        underlying=state.underlying, expiry=state.expiry, ts=state.ts, spot=state.spot,
        verdict=verdict, direction=direction,
        structure_bias=sbias, external_signal=ext, ann_p_win=ann_p_win,
        dq={"verdict": q.get("verdict"), "dqs": q.get("dqs")},
        regime_context=ctx,
        gates=[g.to_dict() for g in gates],
        blocking=blocking, watch_reasons=watch,
        notes=notes + [
            f"structure bias {sbias['bias']} (net {sbias['net']} vs thr {sbias['threshold']})",
            f"external: {ext['source'] + ' ' + ext['direction'] if ext else 'none'}",
            f"ann p_win: {ann_p_win if ann_p_win is not None else 'n/a'}",
            "verdict is research-only -- not wired to any order path",
        ],
    )
    out.summary = _summarize(out)
    return out


def _summarize(q: Qualification) -> str:
    head = f"{q.verdict}"
    if q.verdict != "NO_TRADE" and q.direction != _NEUTRAL:
        head += f" {q.direction}"
    tail = []
    if q.blocking:
        tail.append("blocked by " + ", ".join(q.blocking))
    elif q.watch_reasons:
        tail.append(str(len(q.watch_reasons)) + " soft gate(s): "
                    + "; ".join(r.split(": ", 1)[0] for r in q.watch_reasons))
    tail.append(f"regime {q.regime_context}")
    return f"{head} -- " + " | ".join(tail)


# --------------------------------------------------------------------------- #
def qualify_from_chain(chain, *, external_signal=None, ann_p_win=None,
                       realized_vol=None, cfg=None) -> Qualification:
    """Convenience: run Layer 3 then Layer 5 in one call."""
    from .structure import analyze
    st = analyze(chain, realized_vol=realized_vol, cfg=cfg)
    return qualify(st, external_signal=external_signal, ann_p_win=ann_p_win, cfg=cfg)
