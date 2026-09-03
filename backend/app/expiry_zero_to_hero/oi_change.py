"""
OIChangeEngine — section 4 of the NIFTY cross-expiry brief.

Tests whether OI *change* matters more than absolute OI on/near expiry.

Provenance:
  absolute OI   : ACTUAL  (histcap quote_snapshots, AngelOne `opnInterest`)
  ΔOI / 5m ΔOI  : DERIVED (differenced from consecutive OI snapshots — AngelOne
                  does NOT return `changeinOpenInterest` in the FULL quote, so
                  it can only be derived, never captured directly)
  OI imbalance  : DERIVED from ACTUAL OI
  ΔOI imbalance : DERIVED from DERIVED ΔOI

No sign interpretation is assumed. `classify_oi_action()` combines ΔOI sign +
price move + spot move to label {fresh_writing, fresh_buying, short_covering,
long_unwinding} the standard way, and the lead/lag analyzer (oi_leadlag.py)
then tests whether that label has any predictive value.
"""
from __future__ import annotations


def _diff_over(series, k):
    """series: [(t_index:int, oi:float|None), ...] ascending. Returns list of
    (t_index, oi, d_oi_k) where d_oi_k = oi[i] - oi[i-k] (None if a gap)."""
    vals = [(i, o) for i, o in series]
    out = []
    for j in range(len(vals)):
        i, o = vals[j]
        d = None
        if o is not None and j - k >= 0 and vals[j - k][1] is not None:
            d = o - vals[j - k][1]
        out.append((i, o, d))
    return out


class OIChangeEngine:
    def build(self, ce_oi_series, pe_oi_series, *, minutes_per_step=1.0):
        """ce_oi_series / pe_oi_series: [(minute_index, oi_or_None), ...] on the
        SAME grid. Returns per-step rows with:
          ce_oi, pe_oi (ACTUAL),
          ce_doi_1/5/10, pe_doi_1/5/10 (DERIVED),
          oi_imbalance = (pe_oi - ce_oi)/(pe_oi + ce_oi),
          doi_imbalance_5 = (pe_doi5 - ce_doi5)/(|pe_doi5| + |ce_doi5|),
          oi_acceleration = doi_1 - prev doi_1.
        """
        step5 = max(1, round(5.0 / minutes_per_step))
        step10 = max(1, round(10.0 / minutes_per_step))
        ce1 = _diff_over(ce_oi_series, 1)
        pe1 = _diff_over(pe_oi_series, 1)
        ce5 = _diff_over(ce_oi_series, step5)
        pe5 = _diff_over(pe_oi_series, step5)
        ce10 = _diff_over(ce_oi_series, step10)
        pe10 = _diff_over(pe_oi_series, step10)

        rows = []
        prev_ce_d1 = prev_pe_d1 = None
        for j in range(len(ce1)):
            c_oi, p_oi = ce1[j][1], pe1[j][1]
            imb = None
            if c_oi is not None and p_oi is not None and (c_oi + p_oi) > 0:
                imb = round((p_oi - c_oi) / (p_oi + c_oi), 4)
            cd5, pd5 = ce5[j][2], pe5[j][2]
            doi_imb5 = None
            if cd5 is not None and pd5 is not None and (abs(cd5) + abs(pd5)) > 0:
                doi_imb5 = round((pd5 - cd5) / (abs(pd5) + abs(cd5)), 4)
            ce_acc = (ce1[j][2] - prev_ce_d1) if (ce1[j][2] is not None and prev_ce_d1 is not None) else None
            pe_acc = (pe1[j][2] - prev_pe_d1) if (pe1[j][2] is not None and prev_pe_d1 is not None) else None
            prev_ce_d1, prev_pe_d1 = ce1[j][2], pe1[j][2]
            rows.append({
                "minute_index": ce1[j][0],
                "ce_oi": c_oi, "pe_oi": p_oi, "oi_src": "ACTUAL",
                "ce_doi_1": ce1[j][2], "pe_doi_1": pe1[j][2],
                "ce_doi_5": cd5, "pe_doi_5": pd5,
                "ce_doi_10": ce10[j][2], "pe_doi_10": pe10[j][2],
                "doi_src": "DERIVED",
                "oi_imbalance": imb,
                "doi_imbalance_5": doi_imb5,
                "ce_oi_acceleration": round(ce_acc, 1) if ce_acc is not None else None,
                "pe_oi_acceleration": round(pe_acc, 1) if pe_acc is not None else None,
            })
        return rows


def classify_oi_action(d_oi, d_premium, d_spot):
    """Standard interpretation (to be *tested*, not trusted):
        OI up   + price up   -> fresh BUYING (or fresh writing on the other side)
        OI up   + price down -> fresh WRITING
        OI down + price up   -> SHORT COVERING
        OI down + price down -> LONG UNWINDING
    Returns a label + a bull/bear lean; None if inputs missing."""
    if d_oi is None or d_premium is None:
        return {"label": None, "lean": None}
    up_oi = d_oi > 0
    up_prem = d_premium > 0
    if up_oi and up_prem:
        return {"label": "fresh_buying", "lean": None}
    if up_oi and not up_prem:
        return {"label": "fresh_writing", "lean": None}
    if not up_oi and up_prem:
        return {"label": "short_covering", "lean": None}
    return {"label": "long_unwinding", "lean": None}
