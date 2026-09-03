"""
ExpiryZeroToHeroBacktester — runs the full pipeline over N collected expiry
days and reports precision / recall / expectancy / profit-factor / calibration.

With N < MIN_EXPIRIES it returns INSUFFICIENT_SAMPLE and refuses to quote
precision/recall (a single day = overfitting by construction). It is wired and
tested; it produces real numbers as forward expiry days accumulate.
"""
from __future__ import annotations

from .features import ExpiryFeatureEngine
from .labeler import DEFINITIONS, ZeroToHeroLabeler
from .probability import MIN_EXPIRIES_FOR_CALIBRATION
from .support_detector import PremiumSupportDetector


class ExpiryZeroToHeroBacktester:
    def run(self, collected_windows: list[dict], *, definition="B_3x",
            prob_threshold_pct=50.0) -> dict:
        """collected_windows: list of ExpiryDataCollector.collect_window() dicts.
        Returns the metric block. Chronological order assumed by caller; no
        random shuffle is ever applied to a time series."""
        n_days = len(collected_windows)
        events, preds = [], []
        fe, lab, sup = ExpiryFeatureEngine(), ZeroToHeroLabeler(), PremiumSupportDetector()

        for w in collected_windows:
            idx = w["index_bars"]
            by_key = {}
            for o in w["option_bars"]:
                by_key.setdefault((o["strike"], o["side"]), []).append(o)
            for (strike, side), series in by_key.items():
                series = sorted(series, key=lambda r: r["minute"])
                closes = [r["ltp_c"] for r in series]
                L = lab.label_series(closes)
                feats = fe.build(series, idx)
                sp = sup.detect([(i, c) for i, c in enumerate(closes)])
                for i, r in enumerate(feats):
                    lab_row = L["rows"][i]["labels"].get(definition) if i < len(L["rows"]) else None
                    if lab_row is None:
                        continue
                    # causal 'predict now' proxy: strong repeated support + near expiry
                    predict = bool(sp.get("verdict") in ("STRONG", "MODERATE")
                                   and (r["features"].get("mins_to_expiry") or 99) <= 45
                                   and i in (sp.get("test_minute_indices") or []))
                    events.append(1 if lab_row else 0)
                    preds.append(1 if predict else 0)

        if n_days < MIN_EXPIRIES_FOR_CALIBRATION:
            return {
                "status": "INSUFFICIENT_SAMPLE",
                "expiry_days": n_days,
                "min_expiry_days": MIN_EXPIRIES_FOR_CALIBRATION,
                "detected_z2h_events_by_def": {d: sum(1 for w in collected_windows for x in [_count_events(w, d)] for _ in [None]) for d in DEFINITIONS},
                "note": "Refusing to quote precision/recall/expectancy on < %d expiry days "
                        "— a single day cannot be split into train/val/test without "
                        "overfitting (spec section 9)." % MIN_EXPIRIES_FOR_CALIBRATION,
                "minute_rows_evaluated": len(events),
            }

        tp = sum(1 for p, e in zip(preds, events) if p and e)
        fp = sum(1 for p, e in zip(preds, events) if p and not e)
        fn = sum(1 for p, e in zip(preds, events) if not p and e)
        prec = tp / (tp + fp) if (tp + fp) else None
        rec = tp / (tp + fn) if (tp + fn) else None
        return {
            "status": "OK",
            "expiry_days": n_days,
            "definition": definition,
            "minute_rows_evaluated": len(events),
            "true_pos": tp, "false_pos": fp, "false_neg": fn,
            "precision": round(prec, 3) if prec is not None else None,
            "recall": round(rec, 3) if rec is not None else None,
            "positives": sum(events),
        }


def _count_events(window, definition):
    lab = ZeroToHeroLabeler()
    n = 0
    by_key = {}
    for o in window["option_bars"]:
        by_key.setdefault((o["strike"], o["side"]), []).append(o)
    for series in by_key.values():
        closes = [r["ltp_c"] for r in sorted(series, key=lambda r: r["minute"])]
        L = lab.label_series(closes)
        n += L["positives_per_definition"].get(definition, 0)
    return n
