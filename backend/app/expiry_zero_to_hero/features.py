"""
ExpiryFeatureEngine — per-minute features for one (strike, side) series in the
research window, built ONLY from data that actually exists (see data_collector).

Every feature is causal: at minute t it uses bars <= t only. No look-ahead.
Rolling lookbacks: 1, 2, 3, 5, 10 minutes.
"""
from __future__ import annotations

import statistics as st


def _slope(xs):
    """Least-squares slope of a short series vs index 0..n-1 (per step)."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2.0
    my = sum(xs) / n
    num = sum((i - mx) * (v - my) for i, v in enumerate(xs))
    den = sum((i - mx) ** 2 for i in range(n))
    return num / den if den else 0.0


class ExpiryFeatureEngine:
    LOOKBACKS = (1, 2, 3, 5, 10)

    def build(self, option_series: list[dict], index_series: list[dict]) -> list[dict]:
        """option_series / index_series: minute-sorted dicts from the collector
        for ONE strike+side. Returns the same rows enriched with features_*."""
        idx_by_min = {r["minute"]: r for r in index_series}
        out = []
        pc, sc = [], []                       # rolling premium-close / spot-close
        for r in option_series:
            m = r["minute"]
            ix = idx_by_min.get(m, {})
            p = r.get("ltp_c")
            s = ix.get("spot_c")
            pc.append(p if p is not None else (pc[-1] if pc else None))
            sc.append(s if s is not None else (sc[-1] if sc else None))
            f = {"minute": m, "mins_to_expiry": r.get("mins_to_expiry")}

            for lb in self.LOOKBACKS:
                pw = [x for x in pc[-(lb + 1):] if x is not None]
                sw = [x for x in sc[-(lb + 1):] if x is not None]
                f[f"prem_ret_{lb}m"] = round((pw[-1] - pw[0]), 2) if len(pw) >= 2 else None
                f[f"prem_ret_{lb}m_pct"] = round((pw[-1] / pw[0] - 1) * 100, 2) if len(pw) >= 2 and pw[0] else None
                f[f"spot_ret_{lb}m"] = round((sw[-1] - sw[0]), 2) if len(sw) >= 2 else None

            f["prem_momentum"] = round(_slope([x for x in pc[-4:] if x is not None]), 3)   # 3-min slope
            f["prem_acceleration"] = round(
                _slope([x for x in pc[-3:] if x is not None]) - _slope([x for x in pc[-6:-3] if x is not None]), 3
            ) if len([x for x in pc[-6:] if x is not None]) >= 6 else None
            f["spot_momentum"] = round(_slope([x for x in sc[-4:] if x is not None]), 3)
            f["spot_acceleration"] = round(
                _slope([x for x in sc[-3:] if x is not None]) - _slope([x for x in sc[-6:-3] if x is not None]), 3
            ) if len([x for x in sc[-6:] if x is not None]) >= 6 else None

            # premium compression: recent range / recent mean (small => coiled)
            pw10 = [x for x in pc[-10:] if x is not None]
            f["prem_compression"] = round((max(pw10) - min(pw10)) / (sum(pw10) / len(pw10)), 3) if len(pw10) >= 4 and sum(pw10) else None
            f["prem_range_10m"] = round(max(pw10) - min(pw10), 2) if len(pw10) >= 2 else None

            f["atm_distance_pts"] = round(abs((r.get("strike") or 0) - (s or 0)), 1) if s else None
            f["intrinsic"] = r.get("intrinsic")
            f["time_value"] = r.get("time_value")
            f["iv_model"] = r.get("iv")
            f["delta_model"] = r.get("delta")
            f["gamma_model"] = r.get("gamma")
            f["theta_min_model"] = r.get("theta_per_min")
            # a normalised "gamma-acceleration potential": gamma * spot_speed^2
            sv = f.get("spot_ret_3m")
            if r.get("gamma") is not None and sv is not None:
                f["gamma_accel_potential"] = round(0.5 * r["gamma"] * sv * sv, 2)
            else:
                f["gamma_accel_potential"] = None

            r = {**r, "features": f}
            out.append(r)
        return out
