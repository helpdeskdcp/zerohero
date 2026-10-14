"""Causal Smart-Money-Concepts detectors for the Hybrid MP engine (research only).

Everything at bar i is computed from bars <= i (a prefix test proves it). Definitions (explicit, because "SMC" has no standard):
* major swings: pivots with `swing_len` bars each side (known `swing_len` bars late);  minor swings: `minor_len` (strict).
* Equal highs/lows (EQH/EQL): two major pivots of the same type within `eq_tol_atr`*ATR and `eq_lookback` bars; the liquidity
  level is the higher (EQH) / lower (EQL) of the two.  Plain unswept major pivots are also liquidity (type PLAIN).
* Liquidity sweep: wick through an unswept level and CLOSE back on the original side (high>L & close<L = buy-side sweep -> bearish;
  low<L & close>L = sell-side sweep -> bullish). A close beyond the level is a break, not a sweep; the level is then retired.
* Inducement (IDM): a MINOR pivot inside the current leg (after the last major swing of the opposite kind) that gets swept with a
  close back inside - the small liquidity pool taken before the real move.
* Displacement: body >= `disp_atr`*ATR(prev bar) and body/range >= `disp_body`, in the move direction.
* FVG: 3-candle imbalance (bull: low[i] > high[i-2]); size >= `fvg_min_atr`*ATR; mitigated when a bar CLOSES through it; expires.
* Order block: the last opposite-colour candle within `ob_lookback` bars before a displacement bar that closes beyond the latest
  major swing (structure-confirming); mitigated when a bar closes through it; expires.
* Premium/Discount: dealing range = last confirmed major swing high/low; equilibrium = 50%. Discount = close below it.
* Confirmation score (per side, 0..5): recent sweep, recent inducement sweep, recent displacement, recent touch of an FVG/OB, and
  price in discount (longs) / premium (shorts).  Optional filter: score >= smc_min.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..smart_bos.engine import _atr, _pivots

NAN = float("nan")


@dataclass
class SMCParams:
    swing_len: int = 3
    minor_len: int = 1
    eq_tol_atr: float = 0.10
    eq_lookback: int = 60
    disp_atr: float = 1.2
    disp_body: float = 0.6
    fvg_min_atr: float = 0.10
    fvg_expire: int = 60
    ob_lookback: int = 10
    ob_expire: int = 120
    recent: int = 10
    touch_recent: int = 3
    atr_len: int = 14


def _recent(flag: np.ndarray, k: int) -> np.ndarray:
    """True where `flag` was True within the last k bars (including the current one)."""
    c = np.cumsum(flag.astype(np.int64))
    out = c.copy()
    out[k:] = c[k:] - c[:-k] if len(c) > k else out[k:]
    return out > 0


def smc_context(chart: dict, sp: SMCParams | None = None) -> dict:
    sp = sp or SMCParams()
    o, h, l, c = chart["o"], chart["h"], chart["l"], chart["c"]
    n = len(c)
    atr = _atr(h, l, c, sp.atr_len)
    ph, pl = _pivots(h, sp.swing_len, True, "allow"), _pivots(l, sp.swing_len, False, "allow")
    mh, ml = _pivots(h, sp.minor_len, True, "strict"), _pivots(l, sp.minor_len, False, "strict")
    oL, hL, lL, cL, aL = o.tolist(), h.tolist(), l.tolist(), c.tolist(), atr.tolist()
    phL, plL, mhL, mlL = ph.tolist(), pl.tolist(), mh.tolist(), ml.tolist()

    sweep_bull = np.zeros(n, bool); sweep_bear = np.zeros(n, bool)
    eq_sweep_bull = np.zeros(n, bool); eq_sweep_bear = np.zeros(n, bool)
    idm_bull = np.zeros(n, bool); idm_bear = np.zeros(n, bool)
    disp_bull = np.zeros(n, bool); disp_bear = np.zeros(n, bool)
    fvg_new_bull = np.zeros(n, bool); fvg_new_bear = np.zeros(n, bool)
    fvg_touch_bull = np.zeros(n, bool); fvg_touch_bear = np.zeros(n, bool)
    ob_new_bull = np.zeros(n, bool); ob_new_bear = np.zeros(n, bool)
    ob_touch_bull = np.zeros(n, bool); ob_touch_bear = np.zeros(n, bool)
    eq50 = np.full(n, NAN); dhi = np.full(n, NAN); dlo = np.full(n, NAN)

    last_sh = last_sl = NAN
    last_sh_bar = last_sl_bar = -1
    buy_liq: list = []      # [level, kind('EQ'|'PLAIN'), pivot_bar]   (above price: swept by highs)
    sell_liq: list = []
    minor_hi: list = []     # inducement candidates [level, bar]
    minor_lo: list = []
    fvg_b: list = []        # [bottom, top, birth]
    fvg_s: list = []
    ob_b: list = []
    ob_s: list = []
    piv_hi: list = []       # recent major pivot highs (value, bar) for EQH
    piv_lo: list = []

    for i in range(n):
        a_prev = aL[i - 1] if i > 0 else NAN
        a_now = aL[i]
        # ---- major pivots (known now) ----
        if not math.isnan(phL[i]):
            v, pb = phL[i], i - sp.swing_len
            last_sh, last_sh_bar = v, pb
            tol = (0.0 if math.isnan(a_now) else a_now) * sp.eq_tol_atr
            eq = [u for (u, ub) in piv_hi if pb - ub <= sp.eq_lookback and abs(u - v) <= tol]
            piv_hi.append((v, pb)); piv_hi[:] = piv_hi[-8:]
            if eq and tol > 0:
                buy_liq.append([max(v, *eq), "EQ", pb])
            else:
                buy_liq.append([v, "PLAIN", pb])
            minor_hi[:] = [m for m in minor_hi if m[1] > pb]
        if not math.isnan(plL[i]):
            v, pb = plL[i], i - sp.swing_len
            last_sl, last_sl_bar = v, pb
            tol = (0.0 if math.isnan(a_now) else a_now) * sp.eq_tol_atr
            eq = [u for (u, ub) in piv_lo if pb - ub <= sp.eq_lookback and abs(u - v) <= tol]
            piv_lo.append((v, pb)); piv_lo[:] = piv_lo[-8:]
            if eq and tol > 0:
                sell_liq.append([min(v, *eq), "EQ", pb])
            else:
                sell_liq.append([v, "PLAIN", pb])
            minor_lo[:] = [m for m in minor_lo if m[1] > pb]
        # ---- minor pivots -> inducement candidates (only inside the current leg) ----
        if not math.isnan(mhL[i]) and i - sp.minor_len > last_sh_bar:
            minor_hi.append([mhL[i], i - sp.minor_len])
        if not math.isnan(mlL[i]) and i - sp.minor_len > last_sl_bar:
            minor_lo.append([mlL[i], i - sp.minor_len])
        # ---- sweeps / breaks of liquidity ----
        keep = []
        for lv in buy_liq:
            if hL[i] > lv[0]:
                if cL[i] < lv[0]:
                    sweep_bear[i] = True
                    eq_sweep_bear[i] |= lv[1] == "EQ"
                continue                                # swept or broken -> retired either way
            keep.append(lv)
        buy_liq[:] = keep[-12:]
        keep = []
        for lv in sell_liq:
            if lL[i] < lv[0]:
                if cL[i] > lv[0]:
                    sweep_bull[i] = True
                    eq_sweep_bull[i] |= lv[1] == "EQ"
                continue
            keep.append(lv)
        sell_liq[:] = keep[-12:]
        keep = []
        for m in minor_hi:
            if hL[i] > m[0]:
                if cL[i] < m[0] and i > m[1] + sp.minor_len:
                    idm_bear[i] = True
                continue
            keep.append(m)
        minor_hi[:] = keep[-6:]
        keep = []
        for m in minor_lo:
            if lL[i] < m[0]:
                if cL[i] > m[0] and i > m[1] + sp.minor_len:
                    idm_bull[i] = True
                continue
            keep.append(m)
        minor_lo[:] = keep[-6:]
        # ---- displacement ----
        rng = max(hL[i] - lL[i], 1e-12)
        if not math.isnan(a_prev):
            body = abs(cL[i] - oL[i])
            if body >= sp.disp_atr * a_prev and body / rng >= sp.disp_body:
                if cL[i] > oL[i]:
                    disp_bull[i] = True
                else:
                    disp_bear[i] = True
        # ---- touches of existing zones (born before this bar) ----
        for z in fvg_b:
            if lL[i] <= z[1] and cL[i] >= z[0]:
                fvg_touch_bull[i] = True
        for z in fvg_s:
            if hL[i] >= z[0] and cL[i] <= z[1]:
                fvg_touch_bear[i] = True
        for z in ob_b:
            if lL[i] <= z[1] and cL[i] >= z[0]:
                ob_touch_bull[i] = True
        for z in ob_s:
            if hL[i] >= z[0] and cL[i] <= z[1]:
                ob_touch_bear[i] = True
        # ---- zone maintenance (mitigated by a close through the zone, or expired) ----
        fvg_b[:] = [z for z in fvg_b if cL[i] >= z[0] and i - z[2] <= sp.fvg_expire]
        fvg_s[:] = [z for z in fvg_s if cL[i] <= z[1] and i - z[2] <= sp.fvg_expire]
        ob_b[:] = [z for z in ob_b if cL[i] >= z[0] and i - z[2] <= sp.ob_expire]
        ob_s[:] = [z for z in ob_s if cL[i] <= z[1] and i - z[2] <= sp.ob_expire]
        # ---- new FVG (needs bars i-2..i) ----
        if i >= 2 and not math.isnan(a_prev):
            mn = sp.fvg_min_atr * a_prev
            if lL[i] > hL[i - 2] and lL[i] - hL[i - 2] >= mn:
                fvg_b.append([hL[i - 2], lL[i], i]); fvg_new_bull[i] = True
            if hL[i] < lL[i - 2] and lL[i - 2] - hL[i] >= mn:
                fvg_s.append([hL[i], lL[i - 2], i]); fvg_new_bear[i] = True
        # ---- new order block (displacement that closes beyond the latest major swing) ----
        if disp_bull[i] and not math.isnan(last_sh) and cL[i] > last_sh:
            for k in range(i - 1, max(i - 1 - sp.ob_lookback, -1), -1):
                if cL[k] < oL[k]:
                    ob_b.append([lL[k], hL[k], i]); ob_new_bull[i] = True
                    break
        if disp_bear[i] and not math.isnan(last_sl) and cL[i] < last_sl:
            for k in range(i - 1, max(i - 1 - sp.ob_lookback, -1), -1):
                if cL[k] > oL[k]:
                    ob_s.append([lL[k], hL[k], i]); ob_new_bear[i] = True
                    break
        # ---- dealing range ----
        if not math.isnan(last_sh) and not math.isnan(last_sl) and last_sh > last_sl:
            dhi[i], dlo[i], eq50[i] = last_sh, last_sl, (last_sh + last_sl) / 2.0
    with np.errstate(invalid="ignore"):
        discount = c < eq50
        premium = c > eq50
    r, tr = sp.recent, sp.touch_recent
    comp_bull = np.stack([_recent(sweep_bull, r), _recent(idm_bull, r), _recent(disp_bull, r),
                          _recent(fvg_touch_bull | ob_touch_bull, tr), discount], axis=1)
    comp_bear = np.stack([_recent(sweep_bear, r), _recent(idm_bear, r), _recent(disp_bear, r),
                          _recent(fvg_touch_bear | ob_touch_bear, tr), premium], axis=1)
    return dict(
        sweep_bull=sweep_bull, sweep_bear=sweep_bear, eq_sweep_bull=eq_sweep_bull, eq_sweep_bear=eq_sweep_bear,
        idm_bull=idm_bull, idm_bear=idm_bear, disp_bull=disp_bull, disp_bear=disp_bear,
        fvg_new_bull=fvg_new_bull, fvg_new_bear=fvg_new_bear, fvg_touch_bull=fvg_touch_bull, fvg_touch_bear=fvg_touch_bear,
        ob_new_bull=ob_new_bull, ob_new_bear=ob_new_bear, ob_touch_bull=ob_touch_bull, ob_touch_bear=ob_touch_bear,
        discount=discount, premium=premium, eq50=eq50, dealing_hi=dhi, dealing_lo=dlo,
        comp_bull=comp_bull, comp_bear=comp_bear, bull_score=comp_bull.sum(axis=1), bear_score=comp_bear.sum(axis=1),
        final=dict(fvg_bull=[list(z) for z in fvg_b], fvg_bear=[list(z) for z in fvg_s], ob_bull=[list(z) for z in ob_b],
                   ob_bear=[list(z) for z in ob_s], buy_liquidity=[list(z) for z in buy_liq], sell_liquidity=[list(z) for z in sell_liq]),
    )
