"""
Layer 2 -- OI / IV / Greeks / PCR / Max Pain on a canonical `OptionChain`.

Every function is:
  * pure (takes an `OptionChain`, returns a dataclass / dict)
  * capability-aware -- returns a `method`/`status` tag and `None` fields when
    the chain lacks the inputs (no OI -> no max-pain/PCR/walls; no greeks/IV ->
    no GEX, skew falls back to a solved IV off LTP)
  * single-snapshot -> no look-ahead by construction

Nothing here reaches the network or the DB; it only consumes the chain object
`sources/*` produced. Max-pain is the TRUE writer-payout minimisation, not the
min-OI shortcut the audited repos used.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

from . import bs
from .chain import OptionChain, strike_step_for


def _n(x):
    try:
        f = float(x)
        return f if f == f and math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _atm_idx(chain: OptionChain) -> int | None:
    if not chain.rows:
        return None
    ref = chain.atm_strike if chain.atm_strike is not None else chain.spot
    if ref is None:
        return len(chain.rows) // 2
    return min(range(len(chain.rows)), key=lambda i: abs(chain.rows[i].strike - ref))


def _window_rows(chain: OptionChain, window: int | None):
    if not window or window <= 0:
        return list(chain.rows)
    i = _atm_idx(chain)
    if i is None:
        return list(chain.rows)
    return chain.rows[max(0, i - window): i + window + 1]


# --------------------------------------------------------------------------- #
#  PCR                                                                         #
# --------------------------------------------------------------------------- #
@dataclass
class Pcr:
    status: str
    pcr_oi: float | None = None
    pcr_vol: float | None = None
    pcr_oi_atm: float | None = None
    pcr_vol_atm: float | None = None
    ce_oi: float | None = None
    pe_oi: float | None = None
    ce_vol: float | None = None
    pe_vol: float | None = None
    atm_window: int | None = None
    method: str = "sum(pe)/sum(ce)"

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v is not None}


def _sums(rows, field_):
    ce = sum(v for r in rows if r.ce and (v := _n(getattr(r.ce, field_))) is not None)
    pe = sum(v for r in rows if r.pe and (v := _n(getattr(r.pe, field_))) is not None)
    return ce, pe


def pcr(chain: OptionChain, *, atm_window: int = 10) -> Pcr:
    if not chain.rows or not (chain.capability or {}).get("has_oi", True):
        return Pcr(status="no_oi")
    ce_oi, pe_oi = _sums(chain.rows, "oi")
    ce_v, pe_v = _sums(chain.rows, "volume")
    w = _window_rows(chain, atm_window)
    ce_oi_w, pe_oi_w = _sums(w, "oi")
    ce_v_w, pe_v_w = _sums(w, "volume")
    return Pcr(
        status="ok" if ce_oi > 0 else "thin",
        pcr_oi=round(pe_oi / ce_oi, 4) if ce_oi > 0 else None,
        pcr_vol=round(pe_v / ce_v, 4) if ce_v > 0 else None,
        pcr_oi_atm=round(pe_oi_w / ce_oi_w, 4) if ce_oi_w > 0 else None,
        pcr_vol_atm=round(pe_v_w / ce_v_w, 4) if ce_v_w > 0 else None,
        ce_oi=ce_oi or None, pe_oi=pe_oi or None,
        ce_vol=ce_v or None, pe_vol=pe_v or None,
        atm_window=atm_window)


# --------------------------------------------------------------------------- #
#  Max Pain  (true writer-payout minimisation)                                 #
# --------------------------------------------------------------------------- #
@dataclass
class MaxPain:
    status: str
    max_pain_strike: float | None = None
    spot: float | None = None
    distance_pts: float | None = None
    distance_pct: float | None = None
    total_oi_used: float | None = None
    curve: list = field(default_factory=list)          # [[strike, payout], ...]
    method: str = "argmin_K  Σ ce_oi·max(0,K-Kc) + Σ pe_oi·max(0,Kp-K)"

    def to_dict(self):
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["curve"] = self.curve
        return d


def max_pain(chain: OptionChain) -> MaxPain:
    if not chain.rows or not (chain.capability or {}).get("has_oi", True):
        return MaxPain(status="no_oi")
    strikes = [r.strike for r in chain.rows]
    ce_oi = {r.strike: (_n(r.ce.oi) or 0.0) if r.ce else 0.0 for r in chain.rows}
    pe_oi = {r.strike: (_n(r.pe.oi) or 0.0) if r.pe else 0.0 for r in chain.rows}
    total = sum(ce_oi.values()) + sum(pe_oi.values())
    n_both = sum(1 for r in chain.rows
                 if (r.ce and _n(r.ce.oi)) or (r.pe and _n(r.pe.oi)))
    if total <= 0 or n_both < 4:
        return MaxPain(status="thin", total_oi_used=total or None)

    curve = []
    for K in strikes:
        payout = 0.0
        for Kc, oi in ce_oi.items():
            if oi and K > Kc:
                payout += oi * (K - Kc)
        for Kp, oi in pe_oi.items():
            if oi and Kp > K:
                payout += oi * (Kp - K)
        curve.append([K, payout])
    mp_strike, _ = min(curve, key=lambda kv: kv[1])
    spot = chain.spot
    dist_pts = (mp_strike - spot) if spot else None
    return MaxPain(
        status="ok", max_pain_strike=mp_strike, spot=spot,
        distance_pts=round(dist_pts, 2) if dist_pts is not None else None,
        distance_pct=round(100.0 * dist_pts / spot, 3) if spot else None,
        total_oi_used=total,
        curve=[[k, round(p, 1)] for k, p in curve])


# --------------------------------------------------------------------------- #
#  IV skew / smile                                                             #
# --------------------------------------------------------------------------- #
@dataclass
class IvSkew:
    status: str
    atm_iv: float | None = None
    atm_ce_iv: float | None = None
    atm_pe_iv: float | None = None
    rr_25: float | None = None                 # 25Δ risk-reversal: put_iv - call_iv
    fly_25: float | None = None                # 25Δ butterfly
    skew_slope: float | None = None            # d(pe_iv - ce_iv) / d(moneyness)
    per_strike: list = field(default_factory=list)   # [{strike, ce_iv, pe_iv, skew}]
    iv_source: str = "chain"

    def to_dict(self):
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["per_strike"] = self.per_strike
        return d


def _leg_iv(chain: OptionChain, leg, strike, is_call, T):
    iv = _n(getattr(leg, "iv", None)) if leg else None
    if iv is not None:
        return iv, "chain"
    ltp = _n(getattr(leg, "ltp", None)) if leg else None
    if ltp is not None and chain.spot and T:
        s = bs.implied_vol(ltp, chain.spot, strike, T, is_call)
        if s is not None:
            return s, "bs_solve"
    return None, None


def iv_skew(chain: OptionChain, *, fit_moneyness: float = 0.06) -> IvSkew:
    if not chain.rows:
        return IvSkew(status="empty")
    T = bs.year_fraction(chain.expiry, _iso_to_dt(chain.ts))
    per, src_used = [], set()
    for r in chain.rows:
        civ, cs = _leg_iv(chain, r.ce, r.strike, True, T)
        piv, ps = _leg_iv(chain, r.pe, r.strike, False, T)
        if cs:
            src_used.add(cs)
        if ps:
            src_used.add(ps)
        if civ is None and piv is None:
            continue
        per.append({"strike": r.strike, "ce_iv": civ, "pe_iv": piv,
                    "ce_src": cs, "pe_src": ps,
                    "skew": round(piv - civ, 5) if (civ is not None and piv is not None) else None})
    if not per:
        return IvSkew(status="no_iv")

    i = _atm_idx(chain)
    atm_ce = _n(per_at(per, chain.rows[i].strike, "ce_iv")) if i is not None else None
    atm_pe = _n(per_at(per, chain.rows[i].strike, "pe_iv")) if i is not None else None
    atm_iv = (atm_ce + atm_pe) / 2.0 if (atm_ce and atm_pe) else (atm_ce or atm_pe)

    # slope of (pe_iv - ce_iv) vs log-moneyness -- only from strikes whose BOTH
    # legs carry a source IV (never a deep-ITM bs-solve, which is display-only)
    # and inside a moneyness band so wing noise can't dominate the fit
    xs, ys = [], []
    for p in per:
        if (p["skew"] is None or not chain.spot
                or p["ce_src"] != "chain" or p["pe_src"] != "chain"):
            continue
        m = math.log(p["strike"] / chain.spot)
        if abs(m) > fit_moneyness:
            continue
        xs.append(m)
        ys.append(p["skew"])
    slope = _lin_slope(xs, ys) if len(xs) >= 3 else None

    rr = fly = None
    if any(r.ce and _n(getattr(r.ce, "delta", None)) is not None for r in chain.rows):
        c25 = _iv_at_delta(chain, per, 0.25, is_call=True)
        p25 = _iv_at_delta(chain, per, -0.25, is_call=False)
        if c25 is not None and p25 is not None:
            rr = round(p25 - c25, 5)
            if atm_iv:
                fly = round(0.5 * (p25 + c25) - atm_iv, 5)

    return IvSkew(
        status="ok",
        atm_iv=round(atm_iv, 5) if atm_iv else None,
        atm_ce_iv=round(atm_ce, 5) if atm_ce else None,
        atm_pe_iv=round(atm_pe, 5) if atm_pe else None,
        rr_25=rr, fly_25=fly,
        skew_slope=round(slope, 5) if slope is not None else None,
        per_strike=per,
        iv_source="+".join(sorted(src_used)) or "none")


def per_at(per, strike, key):
    for p in per:
        if abs(p["strike"] - strike) < 1e-6:
            return p.get(key)
    return None


def _iv_at_delta(chain, per, target_delta, *, is_call):
    best = None
    for r in chain.rows:
        leg = r.ce if is_call else r.pe
        d = _n(getattr(leg, "delta", None)) if leg else None
        if d is None:
            continue
        err = abs(d - target_delta)
        iv = per_at(per, r.strike, "ce_iv" if is_call else "pe_iv")
        if iv is not None and (best is None or err < best[0]):
            best = (err, iv)
    return best[1] if best else None


def _lin_slope(xs, ys):
    """Theil-Sen median-of-pairwise-slopes -- robust to the handful of noisy
    deep-OTM broker IVs that wreck an ordinary least-squares skew fit."""
    n = len(xs)
    if n < 3:
        return None
    slopes = sorted((ys[j] - ys[i]) / (xs[j] - xs[i])
                    for i in range(n) for j in range(i + 1, n)
                    if xs[j] != xs[i])
    if not slopes:
        return None
    m = len(slopes)
    return slopes[m // 2] if m % 2 else 0.5 * (slopes[m // 2 - 1] + slopes[m // 2])


def _iso_to_dt(ts):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# --------------------------------------------------------------------------- #
#  OI walls                                                                    #
# --------------------------------------------------------------------------- #
@dataclass
class OiWalls:
    status: str
    resistance: list = field(default_factory=list)     # [{strike, oi, oi_change}] top CE OI
    support: list = field(default_factory=list)         # [{strike, oi, oi_change}] top PE OI
    ce_wall: float | None = None
    pe_wall: float | None = None
    nearest_resistance: float | None = None
    nearest_support: float | None = None
    dist_res_pct: float | None = None
    dist_sup_pct: float | None = None

    def to_dict(self):
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["resistance"], d["support"] = self.resistance, self.support
        return d


def oi_walls(chain: OptionChain, *, k: int = 3) -> OiWalls:
    if not chain.rows or not (chain.capability or {}).get("has_oi", True):
        return OiWalls(status="no_oi")
    ce = [(r.strike, _n(r.ce.oi), _n(getattr(r.ce, "oi_change", None)))
          for r in chain.rows if r.ce and _n(r.ce.oi)]
    pe = [(r.strike, _n(r.pe.oi), _n(getattr(r.pe, "oi_change", None)))
          for r in chain.rows if r.pe and _n(r.pe.oi)]
    if not ce or not pe:
        return OiWalls(status="thin")
    ce_top = sorted(ce, key=lambda t: t[1], reverse=True)[:k]
    pe_top = sorted(pe, key=lambda t: t[1], reverse=True)[:k]
    spot = chain.spot
    res_above = [s for s, _o, _c in ce_top if not spot or s >= spot]
    sup_below = [s for s, _o, _c in pe_top if not spot or s <= spot]
    n_res = min(res_above) if res_above else (ce_top[0][0] if ce_top else None)
    n_sup = max(sup_below) if sup_below else (pe_top[0][0] if pe_top else None)
    return OiWalls(
        status="ok",
        resistance=[{"strike": s, "oi": o, "oi_change": c} for s, o, c in ce_top],
        support=[{"strike": s, "oi": o, "oi_change": c} for s, o, c in pe_top],
        ce_wall=ce_top[0][0], pe_wall=pe_top[0][0],
        nearest_resistance=n_res, nearest_support=n_sup,
        dist_res_pct=round(100.0 * (n_res - spot) / spot, 3) if (spot and n_res) else None,
        dist_sup_pct=round(100.0 * (n_sup - spot) / spot, 3) if (spot and n_sup) else None)


# --------------------------------------------------------------------------- #
#  ΔOI vs an earlier snapshot  (Danish2op time-baseline)                       #
# --------------------------------------------------------------------------- #
def oi_change_vs_baseline(now: OptionChain, baseline: OptionChain) -> dict:
    if not now.rows or not baseline.rows:
        return {"status": "empty", "rows": []}
    base = {r.strike: r for r in baseline.rows}
    rows, net_ce, net_pe = [], 0.0, 0.0
    for r in now.rows:
        b = base.get(r.strike)
        if not b:
            continue
        d = {"strike": r.strike}
        for side in ("ce", "pe"):
            leg_n, leg_b = getattr(r, side), getattr(b, side)
            on = _n(getattr(leg_n, "oi", None)) if leg_n else None
            ob = _n(getattr(leg_b, "oi", None)) if leg_b else None
            if on is not None and ob is not None:
                dv = on - ob
                d[f"{side}_doi"] = dv
                d[f"{side}_doi_pct"] = round(100.0 * dv / ob, 2) if ob else None
                if side == "ce":
                    net_ce += dv
                else:
                    net_pe += dv
        rows.append(d)
    return {"status": "ok" if rows else "no_overlap",
            "baseline_ts": baseline.ts, "now_ts": now.ts,
            "net_ce_doi": net_ce, "net_pe_doi": net_pe, "rows": rows}


# --------------------------------------------------------------------------- #
#  GEX  (gamma exposure profile)  -- mirrors engines/sr_engine._gex_profile    #
# --------------------------------------------------------------------------- #
@dataclass
class Gex:
    status: str
    total_shape: float | None = None
    regime_sign: int = 0                        # -1 / 0 / +1  (dealer net gamma sign)
    flip_strike: float | None = None            # first zero-crossing of shape(K)
    pin_strike: float | None = None             # strike of max |shape|
    sigma: float | None = None
    sigma_src: str | None = None
    t_years: float | None = None
    per_strike: list = field(default_factory=list)
    method: str = "shape(K) = gamma(K)·(ce_oi − pe_oi); sign gated at 10% of |mass|"

    def to_dict(self):
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["per_strike"] = self.per_strike
        return d


def gex(chain: OptionChain, *, iv_floor: float = 0.03, iv_cap: float = 3.0) -> Gex:
    if not chain.rows:
        return Gex(status="empty")
    if not chain.spot or chain.spot <= 0:
        return Gex(status="no_spot")
    if not (chain.capability or {}).get("has_oi", True):
        return Gex(status="no_oi")
    T = bs.year_fraction(chain.expiry, _iso_to_dt(chain.ts))
    if not T:
        return Gex(status="no_expiry")

    rows = [(r.strike, (_n(r.ce.oi) or 0.0) if r.ce else 0.0,
             (_n(r.pe.oi) or 0.0) if r.pe else 0.0,
             _n(getattr(r.ce, "ltp", None)) if r.ce else None,
             _n(getattr(r.pe, "ltp", None)) if r.pe else None)
            for r in chain.rows]
    rows = [t for t in rows if t[1] > 0 or t[2] > 0]
    if len(rows) < 4:
        return Gex(status="thin_chain")

    # one sigma for the whole profile: ATM solved IV, else ATM chain IV
    atm = min(rows, key=lambda t: abs(t[0] - chain.spot))
    sigma, src = None, None
    i = _atm_idx(chain)
    if i is not None:
        row = chain.rows[i]
        for leg in (row.ce, row.pe):
            v = _n(getattr(leg, "iv", None)) if leg else None
            if v and iv_floor <= v <= iv_cap:
                sigma, src = v, "chain_atm_iv"
                break
    if sigma is None:
        for is_call, px in ((True, atm[3]), (False, atm[4])):
            s = bs.implied_vol(px, chain.spot, atm[0], T, is_call, lo=iv_floor, hi=iv_cap)
            if s is not None:
                sigma, src = s, "bs_solve_atm"
                break
    if sigma is None:
        return Gex(status="no_vol")

    per = []
    for K, ce_oi, pe_oi, _cl, _pl in rows:
        g = bs.greeks(chain.spot, K, T, sigma, True)
        gm = g["gamma"] if g else 0.0
        per.append({"strike": K, "ce_oi": ce_oi, "pe_oi": pe_oi,
                    "gamma": round(gm, 10), "shape": round(gm * (ce_oi - pe_oi), 2)})
    total = sum(p["shape"] for p in per)
    absmass = sum(abs(p["shape"]) for p in per) or 1.0
    sign = (1 if total > 0 else -1) if abs(total) > 0.10 * absmass else 0

    pin = max(per, key=lambda p: abs(p["shape"]))["strike"]
    flip = None
    for j in range(1, len(per)):
        a, b = per[j - 1]["shape"], per[j]["shape"]
        if (a > 0 >= b) or (a < 0 <= b):
            ka, kb = per[j - 1]["strike"], per[j]["strike"]
            flip = round(ka + (a / (a - b)) * (kb - ka), 2) if a != b else kb
            break
    return Gex(status="ok", total_shape=round(total, 2), regime_sign=sign,
              flip_strike=flip, pin_strike=pin, sigma=round(sigma, 4), sigma_src=src,
              t_years=round(T, 6), per_strike=per)


# --------------------------------------------------------------------------- #
#  one-call bundle                                                             #
# --------------------------------------------------------------------------- #
def compute_all(chain: OptionChain, *, atm_window: int = 10, walls_k: int = 3) -> dict:
    return {
        "pcr": pcr(chain, atm_window=atm_window),
        "max_pain": max_pain(chain),
        "iv_skew": iv_skew(chain),
        "oi_walls": oi_walls(chain, k=walls_k),
        "gex": gex(chain),
        "strike_step": strike_step_for(chain.underlying),
    }
