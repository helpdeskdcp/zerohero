"""Per-fetch data-quality score for an OptionChain. Multiplicative penalties,
0-100, PASS/WARN/FAIL -- mirrors the l2capture.health philosophy."""
from __future__ import annotations

from datetime import datetime, timezone

from .chain import OptionChain


def _clip(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def _expiry_ok(expiry: str) -> bool:
    for f in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(expiry).upper(), f).date() >= datetime.now(timezone.utc).date()
        except ValueError:
            continue
    return True                                   # unknown format -> don't penalise


def score(chain: OptionChain, *, atm_window: int = 10) -> dict:
    n = len(chain.rows)
    if n == 0:
        return {"dqs": 0.0, "verdict": "FAIL", "reason": "empty chain"}

    win = chain.window(atm_window)
    win_n = max(1, len(win))
    both_ok = sum(1 for r in win
                  if r.ce and r.pe and r.ce.ltp not in (None, 0) and r.pe.ltp not in (None, 0))
    atm_coverage = both_ok / win_n

    legs = [l for r in chain.rows for l in (r.ce, r.pe) if l is not None]
    lg = max(1, len(legs))
    stale = sum(1 for l in legs if l.ltp in (None, 0))
    crossed = sum(1 for l in legs
                  if l.bid not in (None, 0) and l.ask not in (None, 0) and l.bid >= l.ask)
    oi_missing = sum(1 for l in legs if l.oi in (None,))
    cap = chain.capability or {}
    greek_missing = (1.0 - cap.get("greek_coverage", 0.0)) if cap.get("has_greeks") else 0.0

    p = {
        "atm_coverage": _clip(1.0 - atm_coverage) * 0.55,
        "stale": _clip(stale / lg) * 0.35,
        "crossed": _clip(crossed / lg * 3) * 0.25,
        "oi_missing": _clip(oi_missing / lg) * 0.25,
        "greeks": _clip(greek_missing) * 0.15,
        "no_spot": 0.10 if chain.spot is None else 0.0,
        "expired": 0.5 if not _expiry_ok(chain.expiry) else 0.0,
        "thin_chain": _clip(1.0 - n / 20.0) * 0.30,
    }
    dqs = 100.0
    for v in p.values():
        dqs *= (1.0 - v)
    dqs = round(dqs, 1)

    verdict = ("PASS" if (dqs >= 80 and atm_coverage >= 0.9 and chain.spot is not None
                          and _expiry_ok(chain.expiry))
               else "WARN" if dqs >= 55 else "FAIL")
    return {
        "dqs": dqs, "verdict": verdict,
        "n_strikes": n, "atm_window_coverage": round(atm_coverage, 3),
        "stale_pct": round(stale / lg, 3), "crossed_pct": round(crossed / lg, 3),
        "oi_missing_pct": round(oi_missing / lg, 3),
        "greek_coverage": cap.get("greek_coverage", 0.0),
        "spot_present": chain.spot is not None,
        "expiry_valid": _expiry_ok(chain.expiry),
        "penalties": {k: round(v, 4) for k, v in p.items()},
    }
