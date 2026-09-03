"""
ZeroToHeroLabeler — objective, comparable definitions of a "zero to hero" move.
Forward-looking (uses the full session); used ONLY to build research labels,
never as a live input. No definition is privileged — the backtester compares
them and reports which one an operator should adopt.
"""
from __future__ import annotations


DEFINITIONS = {
    "A_2x": {"mult": 2.0},
    "B_3x": {"mult": 3.0},
    "C_5x": {"mult": 5.0},
    "D_pctile": {"pctile": 0.95},   # top-5% forward MFE multiple across the session
}


def _forward_stats(series_after: list[float], entry: float):
    """MFE / MAE / settlement relative to `entry` over everything AFTER an entry
    minute. series_after = premium closes."""
    xs = [x for x in series_after if x is not None]
    if not xs or not entry:
        return None
    mfe = max(xs)
    mae = min(xs)
    settle = xs[-1]
    # max drawdown BEFORE the peak (adverse move an entry would have had to sit through)
    peak_i = xs.index(mfe)
    pre_peak = xs[:peak_i + 1] or [entry]
    dd_before_peak = min(pre_peak)
    time_to_peak = peak_i + 1
    return {
        "entry": round(entry, 2),
        "mfe_abs": round(mfe, 2), "mfe_mult": round(mfe / entry, 3),
        "mae_abs": round(mae, 2), "mae_mult": round(mae / entry, 3),
        "max_drawdown_before_peak_abs": round(dd_before_peak, 2),
        "max_drawdown_before_peak_mult": round(dd_before_peak / entry, 3),
        "time_to_peak_min": time_to_peak,
        "settlement_abs": round(settle, 2), "settlement_mult": round(settle / entry, 3),
        "lost_most_premium": bool(settle <= 0.4 * entry),
    }


class ZeroToHeroLabeler:
    def label_series(self, premium_closes: list[float]) -> dict:
        """Given the full-session premium closes for one strike+side, evaluate an
        entry at EACH minute against every definition and return the per-minute
        label matrix + a session summary."""
        xs = premium_closes
        n = len(xs)
        # session-wide 95th pct forward multiple, for definition D
        all_mults = []
        for i in range(n - 1):
            e = xs[i]
            if not e:
                continue
            fwd = [x for x in xs[i + 1:] if x is not None]
            if fwd:
                all_mults.append(max(fwd) / e)
        d_thr = _pctile(all_mults, 0.95) if all_mults else None

        rows = []
        for i in range(n):
            e = xs[i]
            fstats = _forward_stats(xs[i + 1:], e) if (e and i < n - 1) else None
            labels = {}
            if fstats:
                for name, cfg in DEFINITIONS.items():
                    if "mult" in cfg:
                        labels[name] = fstats["mfe_mult"] >= cfg["mult"]
                    else:
                        labels[name] = (d_thr is not None and fstats["mfe_mult"] >= d_thr)
            rows.append({"minute_index": i, "entry": e, "forward": fstats, "labels": labels})

        pos = {name: sum(1 for r in rows if r["labels"].get(name)) for name in DEFINITIONS}
        return {
            "n_minutes": n,
            "definition_d_threshold_mult": round(d_thr, 3) if d_thr else None,
            "positives_per_definition": pos,
            "rows": rows,
        }


def _pctile(xs, q):
    s = sorted(xs)
    if not s:
        return None
    k = (len(s) - 1) * q
    lo = int(k)
    return s[lo] if lo + 1 >= len(s) else s[lo] + (k - lo) * (s[lo + 1] - s[lo])
