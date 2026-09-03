"""
OI mathematics + wall detection + confluence matrix (sections 6, 7, 22).

Reuses app.expiry_zero_to_hero.oi_change.classify_oi_action for the
CE/PE OI+LTP interpretation matrix. Adds a multiplicative OI-wall score and a
BATTLE_ZONE detector. Nothing here is an absolute classification — every row
carries a confidence.
"""
from __future__ import annotations

from ..expiry_zero_to_hero.oi_change import classify_oi_action


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _norm(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return lambda v: 0.0
    lo, hi = min(xs), max(xs)
    rng = hi - lo or 1e-9
    return lambda v: 0.0 if v is None else max(0.0, min(1.0, (v - lo) / rng))


def oi_matrix(chain: list[dict], spot: float) -> dict:
    """chain rows: {strike, ce_oi, ce_oi_change|ce_oi_chg, ce_ltp, ce_ltp_change,
    pe_*}. Returns {rows:[...], walls:{...}, battle_zone, pcr}.

    Each row: strike, CE/PE OI + %change + LTP + %change + interpretation +
    interpretation_confidence, oi_wall_score, support_score, resistance_score,
    battle_score.
    """
    rows_in = []
    for r in chain or []:
        k = _f(r.get("strike"))
        if k is None:
            continue
        rows_in.append({
            "strike": k,
            "ce_oi": _f(r.get("ce_oi")),
            "ce_doi": _f(r.get("ce_oi_change") if r.get("ce_oi_change") is not None else r.get("ce_oi_chg")),
            "ce_ltp": _f(r.get("ce_ltp")),
            "ce_dltp": _f(r.get("ce_ltp_change")),
            "pe_oi": _f(r.get("pe_oi")),
            "pe_doi": _f(r.get("pe_oi_change") if r.get("pe_oi_change") is not None else r.get("pe_oi_chg")),
            "pe_ltp": _f(r.get("pe_ltp")),
            "pe_dltp": _f(r.get("pe_ltp_change")),
        })
    if len(rows_in) < 3:
        return {"status": "DATA_INSUFFICIENT", "missing": ["option_chain (<3 strikes)"],
                "rows": [], "walls": {}, "battle_zone": None, "pcr": None}

    n_ce_oi = _norm([x["ce_oi"] for x in rows_in])
    n_pe_oi = _norm([x["pe_oi"] for x in rows_in])
    n_ce_doi = _norm([abs(x["ce_doi"]) if x["ce_doi"] is not None else None for x in rows_in])
    n_pe_doi = _norm([abs(x["pe_doi"]) if x["pe_doi"] is not None else None for x in rows_in])

    def _dist_w(k):                       # closer to spot -> heavier
        return max(0.15, 1.0 - min(1.0, abs(k - spot) / (spot * 0.03 or 1)))

    def _pct(cur, d):
        if cur is None or d is None or (cur - d) == 0:
            return None
        return round(d / (cur - d) * 100.0, 2)

    def _interp(doi, dltp):
        lab = classify_oi_action(doi, dltp, None)["label"]
        # confidence from the magnitude of both moves being non-trivial
        conf = 0.0
        if doi is not None and dltp is not None:
            conf = min(1.0, (abs(doi) / (abs(doi) + 1e-9)) * 0.5 + (abs(dltp) > 0) * 0.5)
        return lab, round(conf, 2)

    out_rows, tot_ce, tot_pe = [], 0.0, 0.0
    for x in rows_in:
        k = x["strike"]
        dw = _dist_w(k)
        ce_ltp_conf = 1.0 if (x["ce_dltp"] or 0) != 0 else 0.4
        pe_ltp_conf = 1.0 if (x["pe_dltp"] or 0) != 0 else 0.4
        ce_wall = n_ce_oi(x["ce_oi"]) * (0.4 + 0.6 * n_ce_doi(abs(x["ce_doi"]) if x["ce_doi"] is not None else None)) * ce_ltp_conf * dw
        pe_wall = n_pe_oi(x["pe_oi"]) * (0.4 + 0.6 * n_pe_doi(abs(x["pe_doi"]) if x["pe_doi"] is not None else None)) * pe_ltp_conf * dw
        ce_lab, ce_c = _interp(x["ce_doi"], x["ce_dltp"])
        pe_lab, pe_c = _interp(x["pe_doi"], x["pe_dltp"])
        tot_ce += x["ce_oi"] or 0.0
        tot_pe += x["pe_oi"] or 0.0
        out_rows.append({
            "strike": k,
            "ce_oi": x["ce_oi"], "ce_oi_pct": _pct(x["ce_oi"], x["ce_doi"]),
            "ce_ltp": x["ce_ltp"], "ce_ltp_pct": _pct(x["ce_ltp"], x["ce_dltp"]),
            "ce_interpretation": ce_lab, "ce_interpretation_confidence": ce_c,
            "pe_oi": x["pe_oi"], "pe_oi_pct": _pct(x["pe_oi"], x["pe_doi"]),
            "pe_ltp": x["pe_ltp"], "pe_ltp_pct": _pct(x["pe_ltp"], x["pe_dltp"]),
            "pe_interpretation": pe_lab, "pe_interpretation_confidence": pe_c,
            "ce_wall_score": round(ce_wall * 100, 1),
            "pe_wall_score": round(pe_wall * 100, 1),
            "resistance_score": round(ce_wall * 100, 1),   # CE OI above spot = resistance
            "support_score": round(pe_wall * 100, 1),       # PE OI below spot = support
            "battle_score": round(min(ce_wall, pe_wall) * 200, 1),
        })

    call_wall = max(out_rows, key=lambda r: r["ce_wall_score"])
    put_wall = max(out_rows, key=lambda r: r["pe_wall_score"])
    battle = max(out_rows, key=lambda r: r["battle_score"])
    top3 = sorted(out_rows, key=lambda r: max(r["ce_wall_score"], r["pe_wall_score"]), reverse=True)[:3]
    pcr = round(tot_pe / tot_ce, 3) if tot_ce > 0 else None

    return {
        "status": "OK",
        "rows": out_rows,
        "walls": {
            "CALL_RESISTANCE_WALL": {"strike": call_wall["strike"], "score": call_wall["ce_wall_score"]},
            "PUT_SUPPORT_WALL": {"strike": put_wall["strike"], "score": put_wall["pe_wall_score"]},
            "top3_strikes": [{"strike": r["strike"], "ce_wall": r["ce_wall_score"],
                              "pe_wall": r["pe_wall_score"]} for r in top3],
        },
        "battle_zone": {"strike": battle["strike"], "battle_score": battle["battle_score"]}
        if battle["battle_score"] > 20 else None,
        "pcr": pcr,
    }


def oi_walls_as_levels(matrix: dict) -> list[dict]:
    """Feed OI walls into the level clusterer (confluence.py)."""
    w = (matrix or {}).get("walls") or {}
    out = []
    if w.get("CALL_RESISTANCE_WALL"):
        out.append({"strike": w["CALL_RESISTANCE_WALL"]["strike"], "side": "CE",
                    "kind": "RESISTANCE", "score": w["CALL_RESISTANCE_WALL"]["score"]})
    if w.get("PUT_SUPPORT_WALL"):
        out.append({"strike": w["PUT_SUPPORT_WALL"]["strike"], "side": "PE",
                    "kind": "SUPPORT", "score": w["PUT_SUPPORT_WALL"]["score"]})
    return out
