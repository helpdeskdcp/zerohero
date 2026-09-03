"""
Level -> confluence-zone clusterer (sections 5 & 21).

Two independent levels within `tol` (a % of spot, or an absolute tick) collapse
into one zone. A zone carries its members, an evidence_count (distinct
families), and a strength_score.  "279.40 Pivot + 279.70 Gann + 280.00 OI" ->
one SUPPORT CONFLUENCE ZONE.
"""
from __future__ import annotations


def _default_tol(spot: float) -> float:
    # 0.12% of spot, floored so tiny instruments still cluster sensibly
    return max(spot * 0.0012, spot * 0 + 0.05)


def cluster_levels(levels: list[dict], spot: float, *, tol_pct: float | None = None,
                   tol_abs: float | None = None) -> list[dict]:
    """levels: [{value, family, source, weight}]. Returns zones sorted by
    distance from spot, each:
        {center, zone_low, zone_high, members:[...], evidence_count,
         families:[...], strength_score, side}
    """
    lv = sorted((x for x in levels if x.get("value")), key=lambda x: x["value"])
    if not lv:
        return []
    tol = tol_abs if tol_abs is not None else (spot * (tol_pct or 0.0012) if tol_pct
                                               else _default_tol(spot))

    zones: list[list[dict]] = []
    cur = [lv[0]]
    for x in lv[1:]:
        if x["value"] - cur[-1]["value"] <= tol:
            cur.append(x)
        else:
            zones.append(cur)
            cur = [x]
    zones.append(cur)

    out = []
    for members in zones:
        vals = [m["value"] for m in members]
        wsum = sum(m["weight"] for m in members) or 1e-9
        center = round(sum(m["value"] * m["weight"] for m in members) / wsum, 4)
        fams = sorted({m["family"] for m in members})
        evidence = len(fams)
        # strength: weighted evidence, tighter cluster = better, more distinct
        # families = better. capped 0..100. NOT calibrated.
        spread = (max(vals) - min(vals)) / max(1e-9, tol)
        strength = min(100.0, 18.0 * sum(m["weight"] for m in members)
                       + 12.0 * (evidence - 1)
                       + 15.0 * (1.0 - min(1.0, spread)))
        out.append({
            "center": center,
            "zone_low": round(min(vals), 4),
            "zone_high": round(max(vals), 4),
            "members": [{"value": m["value"], "family": m["family"], "source": m["source"]}
                        for m in members],
            "evidence_count": evidence,
            "families": fams,
            "sources": [m["source"] for m in members],
            "strength_score": round(strength, 1),
            "side": "support" if center <= spot else "resistance",
            "distance_pts": round(abs(center - spot), 2),
            "distance_pct": round(abs(center - spot) / spot * 100.0, 3) if spot else None,
        })
    out.sort(key=lambda z: z["distance_pts"])
    return out


def nearest_zone(zones: list[dict], spot: float, side: str) -> dict | None:
    cand = [z for z in zones if z["side"] == side]
    return min(cand, key=lambda z: z["distance_pts"]) if cand else None


def high_confluence_zones(zones: list[dict], *, min_evidence=3, min_strength=50.0) -> list[dict]:
    """A zone is HIGH-CONFLUENCE only when >= min_evidence INDEPENDENT families
    agree (section 8). Never a signal on its own."""
    return [z for z in zones if z["evidence_count"] >= min_evidence
            and z["strength_score"] >= min_strength]
