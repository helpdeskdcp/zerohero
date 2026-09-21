"""
Phase 8 -- XGBoost feature/label pipeline SCAFFOLDING ONLY. No model is
trained by this module today, and none will be until
check_training_readiness() passes.

LEAKAGE-PREVENTION RULE (hard, enforced by _LABEL_FIELDS below): a
research event's `outcome`, `exit`, `mfe`, and `mae` are the LABEL, never
a feature -- they are only known after the trade window closes, by
definition. `entry`, `target`, and `stop_invalidation` are the trade's own
framing (known at T0) and ARE allowed as features, since a live system
would already know its own planned entry/target/stop before the outcome
exists. Anything from schema.ResearchEvent not in _LABEL_FIELDS is a
candidate feature.

MINIMUM DATASET: 100 independently-labelled events, per the spec's own
Phase 9/13 instruction. Current dataset (see
data/research/reverse_engineering/PHASE0_PHASE1_REPORT.md) is n=5 known
signals -- nowhere close. train() calls the gate FIRST and returns before
importing xgboost at all, so no training code path is even reachable with
today's data.
"""
from __future__ import annotations

MIN_TRAINING_EVENTS = 100

_LABEL_FIELDS = {"outcome", "exit", "mfe", "mae"}


def check_training_readiness(events: list[dict]) -> dict:
    n = len(events)
    if n < MIN_TRAINING_EVENTS:
        return {"model_status": "NOT_READY", "reason": "INSUFFICIENT_SAMPLE",
               "n_events": n, "required": MIN_TRAINING_EVENTS}
    labelled = [e for e in events if e.get("outcome") is not None]
    if len(labelled) < MIN_TRAINING_EVENTS:
        return {"model_status": "NOT_READY", "reason": "INSUFFICIENT_LABELLED_SAMPLE",
               "n_events": n, "n_labelled": len(labelled), "required": MIN_TRAINING_EVENTS}
    return {"model_status": "READY", "n_events": n, "n_labelled": len(labelled)}


def build_feature_vector(event: dict) -> dict:
    """Strips label fields out of a research event, returning only what
    would have been knowable at T0. Never raises on a missing/None field --
    a feature that wasn't captured is simply None, exactly as recorded."""
    return {k: v for k, v in event.items()
           if k not in _LABEL_FIELDS and k not in ("id", "capture_timestamp")}


def build_label(event: dict) -> dict:
    return {k: event.get(k) for k in _LABEL_FIELDS}


def chronological_split(events: list[dict], *, train_frac: float = 0.7) -> dict:
    """Time-ordered train/validation/out-of-sample split (by `timestamp`,
    falling back to insertion order) -- never a random shuffle, which would
    leak future information into training. Used only once readiness passes;
    harmless to call earlier for inspection."""
    ordered = sorted(events, key=lambda e: e.get("timestamp") or "")
    n = len(ordered)
    n_train = int(n * train_frac)
    n_val = int(n * (1 - train_frac) / 2)
    return {
        "train": ordered[:n_train],
        "validation": ordered[n_train:n_train + n_val],
        "out_of_sample": ordered[n_train + n_val:],
    }


def train(events: list[dict]) -> dict:
    """Entry point a future caller would use. Checks readiness FIRST and
    returns immediately if not ready -- xgboost is never imported, and no
    fit() call is reachable, unless/until >=100 labelled events exist."""
    readiness = check_training_readiness(events)
    if readiness["model_status"] != "READY":
        return readiness
    # NOT REACHED with today's dataset. Deliberately left unimplemented --
    # building a real training path now, with no data to validate it
    # against, would itself be premature engineering.
    raise NotImplementedError(
        "training path intentionally not implemented until a real >=100-event "
        "labelled dataset exists to design walk-forward validation against")
