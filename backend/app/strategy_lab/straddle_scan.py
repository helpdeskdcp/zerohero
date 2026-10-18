"""Pre-registered expiry-structure scan: short/long ATM straddle and strangle on NIFTY weekly expiry. PURE functions, READ-ONLY research.

12 hypotheses fixed in advance (below). One observation per expiry; P&L in index POINTS per unit and in bps of spot, NET of an explicit cost model
(flat brokerage, STT on the sold premium, exchange/SEBI/stamp/GST, and a slippage of SLIP points per leg per transaction). A missing bar means NO TRADE
for that expiry (never interpolated). Significance: one-sided t on the per-expiry net P&L, Benjamini-Hochberg across the 12 hypotheses."""
from __future__ import annotations

import math
from dataclasses import dataclass

LOT = 65                       # NIFTY lot (Dhan master 2026); only used to spread the flat brokerage over the premium
SLIP = 0.10                    # points per leg per transaction (about 2 ticks; ATM weekly spreads are wider at the open and into the close)
STEP = 50


@dataclass(frozen=True)
class H:
    name: str
    side: str                  # short | long
    legs: str                  # straddle | strangle
    entry_day: int             # 0 = expiry day, -1 = previous trading day
    entry: str                 # HH:MM bar-open time
    exit: str                  # HH:MM (exit price = close of the bar that ENDS at this time), always on the expiry day


HYPS: list[H] = (
    [H(f"short straddle {t}->1515", "short", "straddle", 0, t, "15:15") for t in ("09:20", "10:30", "12:00", "13:30")]
    + [H(f"long straddle {t}->1515", "long", "straddle", 0, t, "15:15") for t in ("09:20", "10:30", "12:00", "13:30")]
    + [H("short strangle1 09:20->1515", "short", "strangle", 0, "09:20", "15:15"), H("long strangle1 09:20->1515", "long", "strangle", 0, "09:20", "15:15"),
       H("short straddle E-1 14:45->E 09:30", "short", "straddle", -1, "14:45", "09:30"), H("long straddle E-1 14:45->E 09:30", "long", "straddle", -1, "14:45", "09:30")]
)


def cost_points(side: str, entry_prem: float, exit_prem: float, lot: int = LOT) -> float:
    """Round-trip cost in index points per unit for a 2-leg position; entry_prem / exit_prem are the SUMS of both legs' premiums."""
    brokerage = 4 * 20.0 / lot                                  # 2 legs x (entry + exit) orders, flat Rs 20 each
    sold = entry_prem if side == "short" else exit_prem
    bought = exit_prem if side == "short" else entry_prem
    stt = 0.0015 * sold                                          # option sell premium STT 0.15% from 2026-04-01 (0.10% before)
    txn = 0.000356 * (entry_prem + exit_prem)
    sebi = 0.000001 * (entry_prem + exit_prem)
    stamp = 0.00003 * bought
    gst = 0.18 * (brokerage + txn)
    slip = 4 * SLIP                                              # 2 legs x 2 transactions
    return brokerage + stt + txn + sebi + stamp + gst + slip


def _hhmm_add(hhmm: str, minutes: int) -> str:
    h, m = int(hhmm[:2]), int(hhmm[3:])
    t = h * 60 + m + minutes
    return f"{t // 60:02d}:{t % 60:02d}"


def _last_before(series: dict, day: str, key: str):
    """Last traded bar of `day` at or before `key` (last-trade carry): an option that did not trade in the final 5 minutes still has a last price."""
    cands = [k for k in series if k.startswith(day) and k <= key]
    return series[max(cands)] if cands else None


def trade(h: H, exp_day: str, prev_day: str, bars: dict, spot_at, carry_exit: bool = False) -> dict | None:
    """bars[(strike, 'CE'|'PE')][ 'YYYY-MM-DD HH:MM' ] = (o, hi, lo, c) with the bar OPEN time as key. spot_at(day, 'HH:MM') -> index price at that bar open.
    Returns None (no trade) if the spot or any needed bar is missing."""
    eday = exp_day if h.entry_day == 0 else prev_day
    if eday is None:
        return None
    spot = spot_at(eday, h.entry)
    if spot is None:
        return None
    atm = round(spot / STEP) * STEP
    k_ce, k_pe = (atm, atm) if h.legs == "straddle" else (atm + STEP, atm - STEP)
    try:
        e_ce, e_pe = bars[(k_ce, "CE")][f"{eday} {h.entry}"], bars[(k_pe, "PE")][f"{eday} {h.entry}"]
        xk = f"{exp_day} {_hhmm_add(h.exit, -5)}"
        x_ce, x_pe = bars[(k_ce, "CE")].get(xk), bars[(k_pe, "PE")].get(xk)
        carried = 0
        if carry_exit:                                            # explicit, counted, never silent
            if x_ce is None:
                x_ce, carried = _last_before(bars[(k_ce, "CE")], exp_day, xk), carried + 1
            if x_pe is None:
                x_pe, carried = _last_before(bars[(k_pe, "PE")], exp_day, xk), carried + 1
        if x_ce is None or x_pe is None:
            return None
    except KeyError:
        return None
    ep, xp = e_ce[0] + e_pe[0], x_ce[3] + x_pe[3]            # enter at the bar open, exit at the bar close
    gross = (ep - xp) if h.side == "short" else (xp - ep)
    c = cost_points(h.side, ep, xp)
    return {"gross": gross, "cost": c, "net": gross - c, "net_bps": (gross - c) / spot * 1e4, "entry_prem": ep, "exit_prem": xp, "spot": spot, "atm": atm, "carried_legs": carried}


def tstat_one_sided(x: list[float]) -> tuple[float, float, float]:
    """(mean, t, one-sided p for mean>0); normal approximation is NOT used -- Student t via a continued-fraction free approximation is avoided by
    using the exact t CDF through the regularised incomplete beta."""
    n = len(x)
    if n < 3:
        return float("nan"), float("nan"), 1.0
    mu = sum(x) / n
    var = sum((v - mu) ** 2 for v in x) / (n - 1)
    if var <= 0:
        return mu, float("inf") if mu > 0 else float("-inf"), 0.0 if mu > 0 else 1.0
    t = mu / math.sqrt(var / n)
    return mu, t, _t_sf(t, n - 1)


def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-30 else 1e-30)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1 + aa / c; c = c if abs(c) > 1e-30 else 1e-30
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-30 else 1e-30)
        c = 1 + aa / c; c = c if abs(c) > 1e-30 else 1e-30
        de = d * c
        h *= de
        if abs(de - 1) < 1e-12:
            break
    return h


def _betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    return bt * _betacf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - bt * _betacf(b, a, 1 - x) / b


def _t_sf(t: float, df: int) -> float:
    """P(T > t) for Student t with df degrees of freedom."""
    if math.isinf(t):
        return 0.0 if t > 0 else 1.0
    p = 0.5 * _betai(df / 2, 0.5, df / (df + t * t))
    return p if t > 0 else 1 - p
