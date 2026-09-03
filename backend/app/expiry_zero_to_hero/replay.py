"""
ExpiryZeroToHeroReplay — reconstruct one historical expiry window minute by
minute and decompose the observed premium move.

Used as the single validation case (03-Sep-2026 SENSEX). Everything is ACTUAL
(index + option premium candles) or DERIVED:BS (greeks/IV/intrinsic). OI is
UNAVAILABLE for a past minute and is reported as such — the trainer's
"put-side OI dominant" read cannot be verified from historical data.
"""
from __future__ import annotations

from . import bs
from .data_collector import ExpiryDataCollector
from .features import ExpiryFeatureEngine
from .labeler import ZeroToHeroLabeler
from .support_detector import PremiumSupportDetector


def run(sdk, *, index="SENSEX", expiry="03SEP2026", session_date="2026-09-03",
        focus_strike=76500.0, focus_side="PE",
        start_hhmm="14:50", end_hhmm="15:40", n_each_side=3) -> dict:
    coll = ExpiryDataCollector(sdk)
    raw = coll.collect_window(index, expiry, session_date, start_hhmm, end_hhmm, n_each_side)
    idx = raw["index_bars"]
    opt = raw["option_bars"]

    # focus series
    fseries = sorted([o for o in opt if abs(o["strike"] - focus_strike) < 1e-6 and o["side"] == focus_side],
                     key=lambda r: r["minute"])
    feats = ExpiryFeatureEngine().build(fseries, idx)

    closes = [(i, r["ltp_c"]) for i, r in enumerate(fseries)]
    support = PremiumSupportDetector().detect(closes)

    prem_closes = [r["ltp_c"] for r in fseries]
    labels = ZeroToHeroLabeler().label_series(prem_closes)

    # --- the replay table -------------------------------------------------
    idx_by_min = {r["minute"]: r for r in idx}
    table = []
    for r in feats:
        m = r["minute"]
        ix = idx_by_min.get(m, {})
        table.append({
            "time": m,
            "sensex": ix.get("spot_c"),
            "pe_ltp": r["ltp_c"],
            "ce_ltp": _match(opt, focus_strike, "CE", m),
            "pe_oi": "UNAVAILABLE", "ce_oi": "UNAVAILABLE", "d_oi": "UNAVAILABLE",
            "iv_model": r["iv"], "delta_model": r["delta"], "gamma_model": r["gamma"],
            "theta_min_model": r["theta_per_min"],
            "intrinsic": r["intrinsic"], "time_value": r["time_value"],
            "prem_ret_10m": r["features"].get("prem_ret_10m"),
            "spot_ret_10m": r["features"].get("spot_ret_10m"),
            "gamma_accel_potential": r["features"].get("gamma_accel_potential"),
            "compression": r["features"].get("prem_compression"),
        })

    # --- explosion detection (largest forward multiple over the window) ---
    peak_i = max(range(len(prem_closes)), key=lambda i: (prem_closes[i] or 0))
    entry_candidates = [i for i, c in enumerate(prem_closes)
                        if c is not None and abs(c - 60) <= 8 and i < peak_i]
    entry_i = entry_candidates[0] if entry_candidates else max(0, peak_i - 10)
    settle_i = len(prem_closes) - 1

    idx_minutes = [r["minute"] for r in idx]

    def _spot_at(minute):
        r = idx_by_min.get(minute)
        if r and r.get("spot_c") is not None:
            return r["spot_c"]
        # nearest available index minute
        if not idx_minutes:
            return None
        tgt = _hm(minute)
        near = min(idx_minutes, key=lambda mm: abs(_hm(mm) - tgt))
        return (idx_by_min.get(near) or {}).get("spot_c")

    S0 = _spot_at(fseries[entry_i]["minute"])
    S1 = _spot_at(fseries[settle_i]["minute"])
    P0 = prem_closes[entry_i]
    P1 = prem_closes[settle_i]
    m0 = fseries[entry_i]["mins_to_expiry"]
    m1 = fseries[settle_i]["mins_to_expiry"]
    iv0 = fseries[entry_i]["iv"]
    if None in (S0, S1, P0, P1):
        decomp = {"status": "SKIPPED", "reason": f"missing S0={S0} S1={S1} P0={P0} P1={P1}"}
    else:
        decomp = bs.decompose_move(S0=S0, S1=S1, K=focus_strike, is_call=(focus_side == "CE"),
                                   mins0=m0, mins1=m1, prem0=P0, prem1=P1, sigma0=iv0, d_iv=0.0)

    # peak-move decomposition too (entry -> intraday premium high)
    peak_min = fseries[peak_i]["minute"]
    Sp = _spot_at(peak_min)
    if None in (S0, Sp, P0, prem_closes[peak_i]):
        decomp_peak = {"status": "SKIPPED", "reason": f"missing S0={S0} Sp={Sp}"}
    else:
        decomp_peak = bs.decompose_move(S0=S0, S1=Sp, K=focus_strike, is_call=(focus_side == "CE"),
                                        mins0=m0, mins1=fseries[peak_i]["mins_to_expiry"],
                                        prem0=P0, prem1=prem_closes[peak_i], sigma0=iv0, d_iv=0.0)

    return {
        "meta": raw["meta"],
        "focus": {"index": index, "expiry": expiry, "strike": focus_strike, "side": focus_side},
        "premium_support_pattern": support,
        "zero_to_hero_labels_summary": {
            "definition_d_threshold_mult": labels["definition_d_threshold_mult"],
            "positives_per_definition": labels["positives_per_definition"],
        },
        "key_minutes": {
            "entry_reference": {"minute": fseries[entry_i]["minute"], "premium": P0, "sensex": S0,
                                "mins_to_expiry": m0},
            "premium_peak": {"minute": peak_min, "premium": round(prem_closes[peak_i], 2), "sensex": Sp,
                             "mins_to_expiry": fseries[peak_i]["mins_to_expiry"]},
            "settlement_bar": {"minute": fseries[settle_i]["minute"], "premium": P1, "sensex": S1},
        },
        "decomposition_entry_to_settlement": decomp,
        "decomposition_entry_to_peak": decomp_peak,
        "replay_table": table,
    }


def _hm(mm):
    try:
        h, m = mm.split(":"); return int(h) * 60 + int(m)
    except Exception:
        return 0


def _match(opt, strike, side, minute):
    for o in opt:
        if abs(o["strike"] - strike) < 1e-6 and o["side"] == side and o["minute"] == minute:
            return o["ltp_c"]
    return None
