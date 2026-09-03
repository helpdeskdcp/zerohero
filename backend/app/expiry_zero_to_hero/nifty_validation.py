"""
NIFTY cross-expiry validation driver.

Runs the SAME framework (unmodified) on NIFTY, plus the section-4/5 OI-change
experiments. Honest by construction: if no completed NIFTY expiry can be
resolved it says so and runs what it can on the histcap non-expiry session.
"""
from __future__ import annotations

from datetime import datetime

from . import bs
from .data_collector import ExpiryDataCollector
from .features import ExpiryFeatureEngine
from .histcap_source import available_sessions, load_oi_premium
from .labeler import ZeroToHeroLabeler
from .oi_change import OIChangeEngine, classify_oi_action
from .oi_leadlag import OILeadLagAnalyzer
from .support_detector import PremiumSupportDetector


def last_valid_nifty_expiry(sdk) -> dict:
    """Section 1: the most recent COMPLETED NIFTY expiry with resolvable data."""
    rows = [r for r in sdk.search_instruments(symbol="NIFTY", exchange="NFO")
            if r.get("instrumenttype") == "OPTIDX" and r.get("expiry")]
    today = datetime.now().date()

    def _d(e):
        try:
            return datetime.strptime(e, "%d%b%Y").date()
        except Exception:
            return None
    exps = sorted({r["expiry"] for r in rows}, key=lambda e: _d(e) or today)
    past = [e for e in exps if _d(e) and _d(e) < today]
    hist = available_sessions("NIFTY")
    hist_completed = [h for h in hist if _d(h["expiry"]) and _d(h["expiry"]) < today]
    return {
        "past_expiries_resolvable_from_master": past or [],
        "future_expiries": [e for e in exps if _d(e) and _d(e) >= today][:6],
        "histcap_option_sessions": hist,
        "histcap_completed_expiry_sessions": hist_completed,
        "verdict": (
            "NO_RESOLVABLE_COMPLETED_NIFTY_EXPIRY — expired weekly contracts are "
            "purged from the AngelOne master, and histcap's earliest NIFTY option "
            "data (%s) is for a not-yet-expired contract." % (
                (hist[0]["date"] if hist else "none"))
        ) if not past and not hist_completed else "resolvable",
    }


def run(sdk, *, nifty_expiry="08SEP2026", session_date=None) -> dict:
    out = {"section_1_last_valid_expiry": last_valid_nifty_expiry(sdk)}

    # --- histcap non-expiry NIFTY session (real OI) -------------------------
    sess = available_sessions("NIFTY")
    if not sess:
        out["oi_analysis"] = {"status": "NO_HISTCAP_NIFTY_OI"}
        return out
    s = sess[-1]
    sd = session_date or s["date"]
    exp = s["expiry"]
    data = load_oi_premium("NIFTY", sd, exp, n_each_side=3, grid_sec=60)
    if not data:
        out["oi_analysis"] = {"status": "LOAD_FAILED"}
        return out

    atm = data["atm"]
    step = data["step"]
    grid = data["grid_minutes"]
    ce = data["strikes"][atm]["ce"]
    pe = data["strikes"][atm]["pe"]

    # section 4 — OI / ΔOI features on the ATM strike
    oic = OIChangeEngine().build(
        [(i, v) for i, v in enumerate(ce["oi"])],
        [(i, v) for i, v in enumerate(pe["oi"])],
        minutes_per_step=1.0)

    # section 5 — does PE ΔOI(5m) lead PE premium?  and CE?
    lead = OILeadLagAnalyzer()
    pe_doi5 = {r["minute_index"]: r["pe_doi_5"] for r in oic if r["pe_doi_5"] is not None}
    ce_doi5 = {r["minute_index"]: r["ce_doi_5"] for r in oic if r["ce_doi_5"] is not None}
    doi_imb5 = {r["minute_index"]: r["doi_imbalance_5"] for r in oic if r["doi_imbalance_5"] is not None}
    oi_imb = {r["minute_index"]: r["oi_imbalance"] for r in oic if r["oi_imbalance"] is not None}
    pe_prem = {i: v for i, v in enumerate(pe["ltp"]) if v is not None}
    ce_prem = {i: v for i, v in enumerate(ce["ltp"]) if v is not None}

    out["oi_analysis"] = {
        "note": "NON-EXPIRY-DAY session (%s, %s not yet expired). Real ACTUAL OI "
                "from histcap; ΔOI DERIVED. Expiry-day dynamics may differ." % (sd, exp),
        "atm": atm, "step": step, "n_grid_minutes": len(grid),
        "oi_imbalance_atm": _stat([r["oi_imbalance"] for r in oic if r["oi_imbalance"] is not None]),
        "doi_imbalance_5_atm": _stat([r["doi_imbalance_5"] for r in oic if r["doi_imbalance_5"] is not None]),
        "H4_PE_doi5_leads_PE_premium": lead.analyze(signal_by_min=pe_doi5, premium_by_min=pe_prem),
        "H4_CE_doi5_leads_CE_premium": lead.analyze(signal_by_min=ce_doi5, premium_by_min=ce_prem),
        "H3_oi_imbalance_leads_PE_premium": lead.analyze(signal_by_min=oi_imb, premium_by_min=pe_prem),
        "H5_doi_imbalance5_leads_PE_premium": lead.analyze(signal_by_min=doi_imb5, premium_by_min=pe_prem),
        "oi_action_label_sample": _oi_action_labels(oic, pe["ltp"]),
    }

    # section 8 — support detector on the NIFTY ATM PE premium series
    pe_closes = [(i, v) for i, v in enumerate(pe["ltp"])]
    out["premium_support_pattern_NIFTY_ATM_PE"] = PremiumSupportDetector().detect(pe_closes)

    # section 6 — run the UNMODIFIED formula on NIFTY's largest ATM PE move of the day
    prem = [v for v in pe["ltp"] if v is not None]
    if len(prem) > 20:
        # find the entry->peak pair with the biggest multiple
        best = (0, 0, 0.0)
        for i in range(len(pe["ltp"]) - 1):
            e = pe["ltp"][i]
            if not e:
                continue
            fwd = [(j, x) for j, x in enumerate(pe["ltp"][i + 1:], i + 1) if x]
            if not fwd:
                continue
            jpk, xpk = max(fwd, key=lambda t: t[1])
            if xpk / e > best[2]:
                best = (i, jpk, xpk / e)
        i0, i1, mult = best
        # NIFTY spot at those minutes — we lack a 1-min NIFTY index series in
        # histcap options; approximate spot from put-call parity is out of scope.
        out["section_6_formula_on_NIFTY"] = {
            "status": "PARTIAL",
            "reason": "histcap stores per-strike OI+LTP but NOT a 1-min NIFTY "
                      "index series for this session, and the option candle API "
                      "cannot supply OI. decompose_move needs S0/S1 -> deferred "
                      "to the forward collector which pulls the index series too.",
            "largest_ATM_PE_move_observed": {"minute_from": grid[i0], "minute_to": grid[i1],
                                             "premium_from": pe["ltp"][i0], "premium_to": pe["ltp"][i1],
                                             "multiple": round(mult, 3)},
        }
    return out


def _stat(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    xs.sort()
    return {"n": len(xs), "min": round(xs[0], 4), "median": round(xs[len(xs) // 2], 4),
            "max": round(xs[-1], 4), "mean": round(sum(xs) / len(xs), 4)}


def _oi_action_labels(oic_rows, pe_ltp):
    from collections import Counter
    c = Counter()
    for r in oic_rows:
        i = r["minute_index"]
        dprem = None
        if i > 0 and pe_ltp[i] is not None and pe_ltp[i - 1] is not None:
            dprem = pe_ltp[i] - pe_ltp[i - 1]
        lab = classify_oi_action(r["pe_doi_1"], dprem, None)["label"]
        if lab:
            c[lab] += 1
    return dict(c)
