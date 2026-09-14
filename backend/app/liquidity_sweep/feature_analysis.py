"""
Phase 1/2 (index-first brief) -- formal statistical discrimination of
per-signal features between WIN and LOSS outcomes.

TIMEOUT-labeled signals are excluded from WIN/LOSS discrimination (a TIMEOUT
is neither a directional win nor loss -- see probability.label_outcome's own
docstring), matching backtest._accuracy()'s existing "graded" convention.

Categorization is a DOCUMENTED, FIXED rule, decided before looking at any
result, not tuned afterward to make any particular feature look better or
worse:

  UNAVAILABLE : > 30% of graded samples have this feature as None, OR the
                effect size/test could not be computed at all (degenerate
                distribution, too few samples) -- not enough real data to
                judge it.
  STRONG      : |effect size| >= 0.5 (continuous: Cohen's d -- "medium-large"
                by Cohen 1988's own convention; categorical: Cramer's V,
                same [0,1] scale) AND p < 0.01.
  WEAK        : 0.2 <= |effect size| < 0.5 AND p < 0.05.
  MISLEADING  : p < 0.05 (nominally "significant") but |effect size| < 0.2
                (negligible) -- the real trap at n~2000 signals: trivial
                differences become "statistically significant" by sample
                size alone, not because they carry real information.
                Flagged explicitly, never silently folded into WEAK.
  REDUNDANT   : an ADDITIONAL tag (not a replacement category) on a
                STRONG/WEAK continuous feature that has |Pearson r| >= 0.8
                with another STRONG/WEAK continuous feature -- two "real"
                features may be measuring the same underlying thing.
  NONE        : none of the above -- negligible effect AND not significant.

Mutual information (sklearn mutual_info_classif, aware of discrete vs
continuous inputs) is reported ALONGSIDE the effect-size/p-value verdict for
extra diagnostic context, never used to override the category -- MI has no
widely agreed "this much is real signal" cutoff the way Cohen's d does.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from statistics import mean, pstdev

import numpy as np
from scipy import stats as scipy_stats
from sklearn.feature_selection import mutual_info_classif

MISSING_RATE_UNAVAILABLE = 0.30
STRONG_EFFECT = 0.5
WEAK_EFFECT = 0.2
ALPHA_STRONG = 0.01
ALPHA_WEAK = 0.05
REDUNDANT_CORR = 0.8


@dataclass
class FeatureVerdict:
    name: str
    kind: str                 # "continuous" | "boolean" | "categorical"
    n_available: int
    n_missing: int
    missing_rate: float
    win_summary: dict | None
    loss_summary: dict | None
    effect_size_name: str | None
    effect_size: float | None
    test_name: str | None
    p_value: float | None
    mutual_information: float | None
    category: str              # STRONG | WEAK | REDUNDANT | MISLEADING | UNAVAILABLE | NONE
    redundant_with: list       # additional tag, see module docstring
    rationale: str

    def to_dict(self) -> dict:
        return asdict(self)


def cohens_d(a: list[float], b: list[float]) -> float | None:
    """Standard Cohen's d, pooled standard deviation. None (not 0.0) when
    either group has <2 points or pooled variance is 0 -- degenerate, not
    "no effect"."""
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = mean(a), mean(b)
    sa, sb = pstdev(a), pstdev(b)
    na, nb = len(a), len(b)
    denom = na + nb - 2
    if denom <= 0:
        return None
    pooled_var = ((na - 1) * sa ** 2 + (nb - 1) * sb ** 2) / denom
    if pooled_var <= 0:
        return None
    return (ma - mb) / (pooled_var ** 0.5)


def mann_whitney(a: list[float], b: list[float]) -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    try:
        _, p = scipy_stats.mannwhitneyu(a, b, alternative="two-sided")
        return float(p)
    except ValueError:
        return None


def cramers_v_and_p(win_cats: list[str], loss_cats: list[str]) -> tuple[float | None, float | None]:
    """Contingency-table association between a categorical/boolean feature
    and WIN/LOSS. Cramer's V (categorical effect size, [0,1]) + chi-square
    p-value, both None if the table is degenerate (a category present on
    only one side, or fewer than 2 categories total)."""
    cats = sorted(set(win_cats) | set(loss_cats))
    if len(cats) < 2:
        return None, None
    table = np.array([[win_cats.count(c) for c in cats], [loss_cats.count(c) for c in cats]])
    if table.sum() == 0 or (table.sum(axis=0) == 0).any() or (table.sum(axis=1) == 0).any():
        return None, None
    try:
        chi2, p, _, _ = scipy_stats.chi2_contingency(table)
    except ValueError:
        return None, None
    n = table.sum()
    k = min(table.shape) - 1
    if k <= 0 or n <= 0:
        return None, float(p)
    v = (chi2 / (n * k)) ** 0.5
    return float(v), float(p)


def mutual_information_binary(values, outcomes_binary: list[int], *, discrete: bool) -> float | None:
    """sklearn mutual_info_classif between one feature and the WIN(1)/LOSS(0)
    label. `discrete=True` for boolean/label-encoded categorical features."""
    if len(values) < 5:
        return None
    x = np.array(values, dtype=float).reshape(-1, 1)
    y = np.array(outcomes_binary)
    if len(set(y.tolist())) < 2:
        return None
    try:
        mi = mutual_info_classif(x, y, discrete_features=discrete, random_state=0)
        return float(mi[0])
    except ValueError:
        return None


def _categorize(effect_size: float | None, p_value: float | None, missing_rate: float) -> tuple[str, str]:
    if missing_rate > MISSING_RATE_UNAVAILABLE:
        return "UNAVAILABLE", (f"{missing_rate:.0%} of graded samples are missing this feature -- "
                               "not enough real data to judge it")
    if effect_size is None or p_value is None:
        return "UNAVAILABLE", "effect size/p-value could not be computed (degenerate distribution or too few samples)"
    ae = abs(effect_size)
    if ae >= STRONG_EFFECT and p_value < ALPHA_STRONG:
        return "STRONG", f"large effect ({ae:.2f}) and p={p_value:.4g} < {ALPHA_STRONG}"
    if WEAK_EFFECT <= ae < STRONG_EFFECT and p_value < ALPHA_WEAK:
        return "WEAK", f"modest effect ({ae:.2f}) and p={p_value:.4g} < {ALPHA_WEAK}"
    if p_value < ALPHA_WEAK and ae < WEAK_EFFECT:
        return "MISLEADING", (f"p={p_value:.4g} looks 'significant' but effect size is negligible ({ae:.2f}) -- "
                              "likely a large-sample artifact, not real signal")
    return "NONE", f"no meaningful difference (effect={ae:.2f}, p={p_value:.4g})"


def analyze_continuous(name: str, win_values: list, loss_values: list) -> FeatureVerdict:
    win_v = [float(v) for v in win_values if v is not None]
    loss_v = [float(v) for v in loss_values if v is not None]
    total = len(win_values) + len(loss_values)
    n_avail = len(win_v) + len(loss_v)
    missing_rate = 1 - (n_avail / total) if total else 1.0
    d = cohens_d(win_v, loss_v)
    p = mann_whitney(win_v, loss_v)
    combined = win_v + loss_v
    labels = [1] * len(win_v) + [0] * len(loss_v)
    mi = mutual_information_binary(combined, labels, discrete=False) if combined else None
    category, rationale = _categorize(d, p, missing_rate)
    return FeatureVerdict(
        name=name, kind="continuous", n_available=n_avail, n_missing=total - n_avail,
        missing_rate=round(missing_rate, 4),
        win_summary={"n": len(win_v), "mean": round(mean(win_v), 4),
                    "median": round(sorted(win_v)[len(win_v) // 2], 4)} if win_v else None,
        loss_summary={"n": len(loss_v), "mean": round(mean(loss_v), 4),
                     "median": round(sorted(loss_v)[len(loss_v) // 2], 4)} if loss_v else None,
        effect_size_name="cohens_d", effect_size=round(d, 4) if d is not None else None,
        test_name="mann_whitney_u", p_value=round(p, 6) if p is not None else None,
        mutual_information=round(mi, 4) if mi is not None else None,
        category=category, redundant_with=[], rationale=rationale)


def analyze_categorical(name: str, win_values: list, loss_values: list) -> FeatureVerdict:
    win_v = [v for v in win_values if v is not None]
    loss_v = [v for v in loss_values if v is not None]
    total = len(win_values) + len(loss_values)
    n_avail = len(win_v) + len(loss_v)
    missing_rate = 1 - (n_avail / total) if total else 1.0
    v_effect, p = cramers_v_and_p([str(x) for x in win_v], [str(x) for x in loss_v])
    combined = win_v + loss_v
    labels = [1] * len(win_v) + [0] * len(loss_v)
    cats = sorted(set(str(c) for c in combined))
    enc = {c: i for i, c in enumerate(cats)}
    encoded = [enc[str(c)] for c in combined]
    mi = mutual_information_binary(encoded, labels, discrete=True) if combined else None
    category, rationale = _categorize(v_effect, p, missing_rate)
    return FeatureVerdict(
        name=name, kind="boolean" if len(cats) <= 2 else "categorical",
        n_available=n_avail, n_missing=total - n_avail, missing_rate=round(missing_rate, 4),
        win_summary=dict(Counter(str(x) for x in win_v)) if win_v else None,
        loss_summary=dict(Counter(str(x) for x in loss_v)) if loss_v else None,
        effect_size_name="cramers_v", effect_size=round(v_effect, 4) if v_effect is not None else None,
        test_name="chi2_contingency", p_value=round(p, 6) if p is not None else None,
        mutual_information=round(mi, 4) if mi is not None else None,
        category=category, redundant_with=[], rationale=rationale)


def apply_redundancy_tags(continuous_raw: dict[str, list], verdicts: dict[str, FeatureVerdict]) -> list[dict]:
    """Pearson correlation among CONTINUOUS features that are themselves
    already STRONG or WEAK -- tags (does not demote) each member of a highly
    correlated pair with `redundant_with`, in place, and returns the flat
    pair list for reporting."""
    names = [n for n, v in verdicts.items() if v.category in ("STRONG", "WEAK") and n in continuous_raw]
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            xa, xb = continuous_raw[a], continuous_raw[b]
            paired = [(float(x), float(y)) for x, y in zip(xa, xb) if x is not None and y is not None]
            if len(paired) < 5:
                continue
            xs, ys = zip(*paired)
            if pstdev(xs) == 0 or pstdev(ys) == 0:
                continue
            r = float(np.corrcoef(xs, ys)[0, 1])
            if abs(r) >= REDUNDANT_CORR:
                pairs.append({"a": a, "b": b, "pearson_r": round(r, 4)})
                verdicts[a].redundant_with.append(b)
                verdicts[b].redundant_with.append(a)
    return pairs


CONTINUOUS_FEATURES = (
    "setup_score", "passed_checks", "htf_score", "sweep_reaction", "sweep_reaction_atr_ratio",
    "bars_to_reclaim", "rsi14", "macd", "adx", "atr14", "volume", "volume_ratio",
    "probability", "confidence", "rr", "risk_amount", "reward_amount",
)
CATEGORICAL_FEATURES = (
    "direction", "regime", "structure_type", "cisd", "fvg", "order_block", "sweep_kind",
    "level_source", "chk_structure_aligned", "chk_secondary_confirmation", "chk_htf_aligned",
    "chk_vwap_aligned", "chk_ema_aligned", "chk_rsi_not_extreme", "chk_adx_trending",
    "chk_volume_above_average", "above_vwap", "above_ema20", "ema20_gt_ema50",
    "time_bucket", "day_of_week",
)


def analyze_features(samples: list) -> dict:
    """`samples`: list of objects with a `.to_dict()` (Stage1FeatureSample)
    and an `.outcome` of WIN/LOSS/TIMEOUT. Returns a full per-feature report
    plus redundancy pairs and a category-count summary."""
    graded = [s for s in samples if s.outcome in ("WIN", "LOSS")]
    win = [s.to_dict() for s in graded if s.outcome == "WIN"]
    loss = [s.to_dict() for s in graded if s.outcome == "LOSS"]

    verdicts: dict[str, FeatureVerdict] = {}
    continuous_raw: dict[str, list] = {}
    for name in CONTINUOUS_FEATURES:
        win_vals = [row.get(name) for row in win]
        loss_vals = [row.get(name) for row in loss]
        continuous_raw[name] = win_vals + loss_vals
        verdicts[name] = analyze_continuous(name, win_vals, loss_vals)
    for name in CATEGORICAL_FEATURES:
        win_vals = [row.get(name) for row in win]
        loss_vals = [row.get(name) for row in loss]
        verdicts[name] = analyze_categorical(name, win_vals, loss_vals)

    redundancy_pairs = apply_redundancy_tags(continuous_raw, verdicts)
    for name, v in verdicts.items():
        if v.redundant_with and v.category != "REDUNDANT":
            v.rationale += f" (also flagged REDUNDANT with {', '.join(sorted(set(v.redundant_with)))})"

    counts = Counter(v.category for v in verdicts.values())
    return {
        "n_total_signals": len(samples),
        "n_win": len(win), "n_loss": len(loss),
        "n_timeout": len(samples) - len(win) - len(loss),
        "features": {name: v.to_dict() for name, v in verdicts.items()},
        "redundancy_pairs": redundancy_pairs,
        "category_counts": dict(counts),
    }
