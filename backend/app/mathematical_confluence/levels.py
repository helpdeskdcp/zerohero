"""
MATHEMATICAL_CONFLUENCE_ENGINE_V1 — level mathematics.

Classical floor pivots (section 2) reuse the exact formula already in
turning_point_engine._pivots. Gann balance/range levels (section 3) are the
genuinely-new bit; they are labelled "GANN MATHEMATICAL LEVEL", never
"guaranteed target".

Every level is emitted as a dict:
    {"value": float, "family": str, "source": str, "weight": float}
so the confluence clusterer (confluence.py) can treat them uniformly.
"""
from __future__ import annotations

from ..engines.turning_point_engine import _pivots as _classical_pivots


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def classical_pivots(pdh, pdl, pdc) -> dict | None:
    """P = (PDH+PDL+PDC)/3 ; R1=2P-PDL ; S1=2P-PDH ; R2=P+(PDH-PDL) ;
    S2=P-(PDH-PDL) ; R3=PDH+2(P-PDL) ; S3=PDL-2(PDH-P). Reuses the codebase
    implementation so there is one source of truth."""
    p = _classical_pivots([pdh, pdl, pdc])
    if not p:
        return None
    return {"pivot": p["pp"], "r1": p["r1"], "r2": p["r2"], "r3": p["r3"],
            "s1": p["s1"], "s2": p["s2"], "s3": p["s3"]}


def gann_levels(pdh, pdl) -> dict | None:
    """GANN MATHEMATICAL LEVELS (section 3) — NOT guaranteed Gann targets.
    balance = (PDH+PDL)/2 ; up/down k = balance +/- range*k/4  for k in 1..4."""
    H, L = _f(pdh), _f(pdl)
    if None in (H, L) or H <= L:
        return None
    balance = (H + L) / 2.0
    rng = H - L
    out = {"gann_balance": round(balance, 4), "range": round(rng, 4)}
    for k in (1, 2, 3, 4):
        out[f"gann_up_{k}"] = round(balance + rng * k / 4.0, 4)
        out[f"gann_down_{k}"] = round(balance - rng * k / 4.0, 4)
    return out


# family -> default confluence weight (evidence strength of that source type)
_FAMILY_WEIGHT = {
    "prev_day": 1.0, "pivot": 0.9, "pivot_r1s1": 0.85, "pivot_r2s2": 0.7,
    "pivot_r3s3": 0.55, "gann_balance": 0.8, "gann": 0.55, "today_open": 0.7,
    "swing": 0.8, "oi_wall": 1.0, "oi_zone": 0.85,
}


def normalized_levels(*, pdh=None, pdl=None, pdc=None, today_open=None,
                      day_high=None, day_low=None,
                      swing_high=None, swing_low=None,
                      recent_swing_high=None, recent_swing_low=None,
                      oi_support=None, oi_resistance=None,
                      oi_walls: list | None = None) -> list[dict]:
    """Flatten every mathematical + structural + OI level into one list of
    {value, family, source, weight}. Missing inputs are simply skipped —
    never fabricated (section 28)."""
    lv: list[dict] = []

    def add(value, family, source, w=None):
        v = _f(value)
        if v is None or v <= 0:
            return
        lv.append({"value": round(v, 4), "family": family, "source": source,
                   "weight": w if w is not None else _FAMILY_WEIGHT.get(family, 0.5)})

    add(pdh, "prev_day", "PDH")
    add(pdl, "prev_day", "PDL")
    add(pdc, "prev_day", "PDC", 0.8)
    add(today_open, "today_open", "TODAY_OPEN")
    add(day_high, "swing", "DAY_HIGH", 0.7)
    add(day_low, "swing", "DAY_LOW", 0.7)
    add(swing_high, "swing", "INTRADAY_SWING_HIGH")
    add(swing_low, "swing", "INTRADAY_SWING_LOW")
    add(recent_swing_high, "swing", "RECENT_SWING_HIGH", 0.65)
    add(recent_swing_low, "swing", "RECENT_SWING_LOW", 0.65)

    piv = classical_pivots(pdh, pdl, pdc)
    if piv:
        add(piv["pivot"], "pivot", "PIVOT")
        add(piv["r1"], "pivot_r1s1", "R1"); add(piv["s1"], "pivot_r1s1", "S1")
        add(piv["r2"], "pivot_r2s2", "R2"); add(piv["s2"], "pivot_r2s2", "S2")
        add(piv["r3"], "pivot_r3s3", "R3"); add(piv["s3"], "pivot_r3s3", "S3")

    g = gann_levels(pdh, pdl)
    if g:
        add(g["gann_balance"], "gann_balance", "GANN_BALANCE")
        for k in (1, 2, 3, 4):
            add(g[f"gann_up_{k}"], "gann", f"GANN_MATH_UP_{k}")
            add(g[f"gann_down_{k}"], "gann", f"GANN_MATH_DOWN_{k}")

    add(oi_support, "oi_zone", "OI_SUPPORT_STRIKE")
    add(oi_resistance, "oi_zone", "OI_RESISTANCE_STRIKE")
    for w in (oi_walls or []):
        add(w.get("strike"), "oi_wall", f"OI_WALL_{w.get('side','')}_{w.get('kind','')}",
            min(1.2, 0.6 + (w.get("score") or 0) / 100.0))
    return lv
