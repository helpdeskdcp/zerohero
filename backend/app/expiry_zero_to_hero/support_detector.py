"""
PremiumSupportDetector — the "repeated premium-level test" pattern, discovered,
never hard-coded to a number.

A support test = the premium falls to within `tol` of a level, then rebounds by
at least `min_bounce`. Two or more tests of the *same* level (within tol) that
are separated in time and get progressively shallower/compressed is the setup
the trainer described (₹81 -> ₹60 -> bounce -> ~₹59 -> bounce -> expansion).
"""
from __future__ import annotations


class PremiumSupportDetector:
    def __init__(self, *, tol_pct=0.06, tol_abs=4.0, min_bounce_pct=0.08,
                 min_gap_min=2, max_gap_min=40):
        self.tol_pct = tol_pct
        self.tol_abs = tol_abs
        self.min_bounce_pct = min_bounce_pct
        self.min_gap_min = min_gap_min
        self.max_gap_min = max_gap_min

    def detect(self, premium_closes: list[tuple]) -> dict:
        """premium_closes: [(minute_index:int, close:float), ...] causal order.
        Returns the strongest repeated-support cluster found, or a null verdict.
        """
        pts = [(i, c) for i, c in premium_closes if c is not None]
        if len(pts) < 6:
            return self._null("too_few_points")

        # local minima with a rebound
        troughs = []
        for k in range(1, len(pts) - 1):
            i, c = pts[k]
            if c <= pts[k - 1][1] and c <= pts[k + 1][1]:
                # forward rebound within the next few minutes
                fwd = [p for j, p in pts[k + 1:k + 6]]
                if fwd and (max(fwd) - c) / c >= self.min_bounce_pct:
                    troughs.append((i, c, round((max(fwd) - c) / c, 3)))
        if len(troughs) < 2:
            return self._null("no_repeated_trough", troughs=len(troughs))

        # cluster troughs whose level is within tolerance of each other
        best = None
        for a in range(len(troughs)):
            lvl = troughs[a][1]
            tol = max(self.tol_abs, lvl * self.tol_pct)
            cluster = [t for t in troughs if abs(t[1] - lvl) <= tol]
            if len(cluster) < 2:
                continue
            gaps = [cluster[j + 1][0] - cluster[j][0] for j in range(len(cluster) - 1)]
            if any(g < self.min_gap_min or g > self.max_gap_min for g in gaps):
                continue
            level = round(sum(t[1] for t in cluster) / len(cluster), 2)
            bounces = [t[2] for t in cluster]
            compression = round((max(t[1] for t in cluster) - min(t[1] for t in cluster)) / level, 3)
            # strength: more tests + tighter cluster + steady/again bouncing
            strength = min(100, int(30 * (len(cluster) - 1) + 40 * (1 - min(1.0, compression * 5))
                                    + 30 * min(1.0, sum(bounces) / len(bounces) / 0.15)))
            cand = {
                "support_level": level,
                "support_tolerance": round(tol, 2),
                "number_of_tests": len(cluster),
                "test_minute_indices": [t[0] for t in cluster],
                "bounce_sizes_pct": bounces,
                "time_between_tests_min": gaps,
                "premium_compression": compression,
                "strength": strength,
                "verdict": ("STRONG" if strength >= 65 else "MODERATE" if strength >= 40 else "WEAK"),
            }
            if best is None or cand["strength"] > best["strength"]:
                best = cand
        return best or self._null("no_valid_cluster")

    @staticmethod
    def _null(reason, **kw):
        return {"support_level": None, "number_of_tests": 0, "strength": 0,
                "verdict": "NONE", "reason": reason, **kw}
