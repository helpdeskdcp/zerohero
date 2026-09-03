"""
OILeadLagAnalyzer — section 5: does ΔOI LEAD option-premium movement?

For every timestamp T (using only info available at T) it measures the forward
premium move over +1/2/3/5/10 minutes and reports, per horizon:
  - Pearson correlation( signal@T , forward_return )
  - directional accuracy ( sign(signal) == sign(forward_return) )
  - conditional P( |forward_return| >= expansion_thr | signal in top tercile )
  - a lead / coincident / lag verdict

`signal` is pluggable: pe_doi_5, doi_imbalance_5, oi_imbalance, oi_acceleration…
No sign interpretation is baked in — the report shows the raw correlation and
lets the numbers speak.
"""
from __future__ import annotations

import math

HORIZONS = (1, 2, 3, 5, 10)


def _pearson(xs, ys):
    n = len(xs)
    if n < 4:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return 0.0
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(cov / math.sqrt(sx * sy), 4)


def _tercile_hi(xs):
    s = sorted(abs(x) for x in xs)
    return s[int(len(s) * 2 / 3)] if s else 0.0


class OILeadLagAnalyzer:
    def analyze(self, *, signal_by_min: dict, premium_by_min: dict,
                expansion_thr_pct: float = 15.0, minutes_per_step: float = 1.0):
        """signal_by_min / premium_by_min: {minute_index: value}. Returns a
        block per horizon + an overall lead/lag verdict."""
        idxs = sorted(set(signal_by_min) & set(premium_by_min))
        step = {h: max(1, round(h / minutes_per_step)) for h in HORIZONS}
        results = {}
        for h in HORIZONS:
            k = step[h]
            sig, fwd = [], []
            hits = tot = 0
            for j, i in enumerate(idxs):
                if j + k >= len(idxs):
                    break
                s = signal_by_min.get(i)
                p0 = premium_by_min.get(i)
                p1 = premium_by_min.get(idxs[j + k])
                if s is None or p0 in (None, 0) or p1 is None:
                    continue
                r = (p1 / p0 - 1.0) * 100.0
                sig.append(s)
                fwd.append(r)
                tot += 1
            if tot < 6:
                results[f"+{h}m"] = {"n": tot, "status": "TOO_FEW"}
                continue
            corr = _pearson(sig, fwd)
            # directional accuracy on non-tiny signals
            da_n = da_hit = 0
            thr = _tercile_hi(sig)
            cond_n = cond_hit = 0
            for s, r in zip(sig, fwd):
                if abs(s) >= 1e-9:
                    da_n += 1
                    da_hit += 1 if (s > 0) == (r > 0) else 0
                if abs(s) >= thr and thr > 0:
                    cond_n += 1
                    cond_hit += 1 if abs(r) >= expansion_thr_pct else 0
            results[f"+{h}m"] = {
                "n": tot,
                "pearson_corr": corr,
                "directional_accuracy": round(da_hit / da_n, 3) if da_n else None,
                "P(expansion | signal_top_tercile)": round(cond_hit / cond_n, 3) if cond_n else None,
                "base_rate_expansion": round(sum(1 for r in fwd if abs(r) >= expansion_thr_pct) / tot, 3),
            }
        # lead/lag verdict: is |corr| bigger at +Nm (lead) than at 0/-? here we
        # only test forward, so a rising |corr| into +3..+5m with da>0.55 => LEAD.
        corrs = {h: (results[f"+{h}m"].get("pearson_corr") or 0) for h in HORIZONS
                 if isinstance(results[f"+{h}m"], dict) and "pearson_corr" in results[f"+{h}m"]}
        das = {h: (results[f"+{h}m"].get("directional_accuracy") or 0) for h in HORIZONS
               if isinstance(results[f"+{h}m"], dict) and results[f"+{h}m"].get("directional_accuracy")}
        peak_h = max(corrs, key=lambda h: abs(corrs[h])) if corrs else None
        best_corr = corrs.get(peak_h, 0) if peak_h else 0
        best_da = max(das.values()) if das else 0
        if abs(best_corr) < 0.1 and best_da < 0.55:
            verdict = "STATISTICALLY_WEAK"
        elif peak_h and peak_h >= 3:
            verdict = "LEADING (peak forward corr at +%dm)" % peak_h
        else:
            verdict = "COINCIDENT_OR_SHORT_LEAD"
        return {"horizons": results, "peak_corr_horizon": peak_h,
                "peak_corr": best_corr, "best_directional_accuracy": best_da,
                "verdict": verdict, "n_timestamps": len(idxs)}
