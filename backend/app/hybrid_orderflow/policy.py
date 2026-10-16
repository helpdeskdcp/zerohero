"""Load + validate strategy_policy.json. FAIL CLOSED: any missing/invalid/unsafe setting raises PolicyError, and callers must treat
PolicyError as NO_TRADE(POLICY_INVALID). Live execution can never be enabled through this file (validator rejects it)."""
from __future__ import annotations

import json
import os
import re

POLICY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy_policy.json")
SUPPORTED_MAJOR = 1
_REQUIRED = ("policy_version", "validation_status", "change_history", "execution", "markets", "data", "features", "smc",
             "scoring", "options", "risk", "no_trade")
_SCORE_KEYS = {"structure", "liquidity_sweep", "delta_cvd", "imbalance_absorption", "vwap_regime", "relative_volume", "htf_alignment"}


class PolicyError(ValueError):
    pass


def _need(cond, msg):
    if not cond:
        raise PolicyError(msg)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate(p: dict) -> dict:
    _need(isinstance(p, dict), "policy must be an object")
    for k in _REQUIRED:
        _need(k in p, f"missing section: {k}")
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(p["policy_version"]))
    _need(m is not None, "policy_version must be semver")
    _need(int(m.group(1)) == SUPPORTED_MAJOR, f"unsupported policy major version {m.group(1)} (engine supports {SUPPORTED_MAJOR})")
    _need(p["validation_status"] in ("DRAFT_UNVALIDATED", "VALIDATED_PAPER", "VALIDATED_LIVE"), "bad validation_status")
    _need(isinstance(p["change_history"], list) and p["change_history"], "change_history must be non-empty")
    ex = p["execution"]
    _need(ex.get("mode") in ("PAPER", "SIGNAL_ONLY"), "execution.mode must be PAPER or SIGNAL_ONLY (live is not permitted by this policy)")
    _need(ex.get("live_orders_enabled") is False, "execution.live_orders_enabled must be exactly false")
    _need(ex.get("kill_switch_required") is True, "execution.kill_switch_required must be true")
    d = p["data"]
    _need(_num(d.get("max_quote_age_sec")) and 0 < d["max_quote_age_sec"] <= 60, "data.max_quote_age_sec must be in (0,60]")
    _need(_num(d.get("max_candle_age_sec")) and d["max_candle_age_sec"] > 0, "data.max_candle_age_sec invalid")
    _need(d.get("require_closed_candle") is True, "data.require_closed_candle must be true")
    _need(d.get("no_forward_fill_missing_bars") is True, "data.no_forward_fill_missing_bars must be true")
    _need(d.get("provider_priority") and all(isinstance(x, str) for x in d["provider_priority"]), "data.provider_priority invalid")
    f = p["features"]
    _need("OHLC_PROXY" in f["delta"].get("forbidden_classes", []), "features.delta must forbid OHLC_PROXY as delta")
    _need(_num(f["footprint"].get("stack_ratio")) and f["footprint"]["stack_ratio"] > 1, "footprint.stack_ratio must be > 1")
    _need(isinstance(f["footprint"].get("stack_min_run"), int) and f["footprint"]["stack_min_run"] >= 2, "footprint.stack_min_run must be >= 2")
    _need(f["dom"].get("reject_crossed_book") is True, "dom.reject_crossed_book must be true")
    s = p["smc"]
    _need(s.get("bos_requires") == "CANDLE_CLOSE_BEYOND_LEVEL" and s.get("choch_requires") == "CANDLE_CLOSE_BEYOND_LEVEL", "BOS/CHoCH must require candle close")
    _need(s.get("never_rewrite_past_signals") is True, "smc.never_rewrite_past_signals must be true")
    sc = p["scoring"]
    _need(set(sc.get("components_max", {})) == _SCORE_KEYS, "scoring.components_max keys mismatch")
    total = sum(sc["components_max"].values())
    _need(all(_num(v) and v >= 0 for v in sc["components_max"].values()), "scoring component max must be >= 0")
    _need(_num(sc.get("min_total_score")) and 0 < sc["min_total_score"] <= total, "scoring.min_total_score must be in (0, total]")
    _need(sc.get("is_probability") is False, "scoring must not be represented as a probability")
    _need(isinstance(sc.get("min_distinct_evidence_groups"), int) and sc["min_distinct_evidence_groups"] >= 2, "need >= 2 distinct evidence groups")
    _need(sc.get("conflict_rule") in ("WAIT_IF_ANY_OPPOSING", "WAIT_IF_ANY_OPPOSING_MANDATORY"), "scoring.conflict_rule invalid")
    _need(sc.get("mandatory_components") and set(sc["mandatory_components"]) <= _SCORE_KEYS, "scoring.mandatory_components invalid")
    members = [c for g in sc.get("groups", {}).values() for c in g]
    _need(sorted(members) == sorted(_SCORE_KEYS), "scoring.groups must cover every component exactly once")
    _need(all(g in sc["groups"] and _num(c) and c > 0 for g, c in sc.get("group_caps", {}).items()), "scoring.group_caps invalid")
    er = sc.get("evidence_rules", {})
    _need(set(_SCORE_KEYS) <= set(er), "scoring.evidence_rules must define every component")
    _need(0 < er["delta_cvd"]["delta_pct_1pt"] < er["delta_cvd"]["delta_pct_2pt"] <= 1, "delta_cvd thresholds must satisfy 0 < 1pt < 2pt <= 1")
    _need(er["structure"]["BOS"] <= sc["components_max"]["structure"] and er["structure"]["CHOCH"] <= sc["components_max"]["structure"], "structure points exceed component max")
    _need(er["liquidity_sweep"]["full_points_fast"] <= sc["components_max"]["liquidity_sweep"], "sweep points exceed component max")
    _need(er["imbalance_absorption"]["require_adjacency_verified"] is True, "imbalance must require verified adjacency")
    _need(_num(sc.get("duplicate_window_sec")) and sc["duplicate_window_sec"] > 0, "scoring.duplicate_window_sec must be > 0")
    o = p["options"]
    _need(o.get("direction_map") == {"BULLISH": "CE", "BEARISH": "PE"}, "options.direction_map must be BULLISH->CE, BEARISH->PE")
    _need(o.get("reject_expired") is True, "options.reject_expired must be true")
    _need(o.get("atm_tie_break") in ("LOWER_STRIKE", "HIGHER_STRIKE"), "options.atm_tie_break invalid")
    _need(set(o.get("preference_order", [])) <= {"ATM", "ITM", "OTM"} and o["preference_order"], "options.preference_order invalid")
    _need(o.get("otm_enabled") is True or "OTM" not in o["preference_order"], "OTM in preference_order but otm_enabled is false")
    for band, rng in o.get("delta_bands", {}).items():
        _need(band in ("ITM", "ATM", "OTM") and len(rng) == 2 and 0 <= rng[0] < rng[1] <= 1, f"bad delta band {band}")
    _need(_num(o.get("max_spread_pct")) and 0 < o["max_spread_pct"] <= 25, "options.max_spread_pct invalid")
    r = p["risk"]
    _need(_num(r.get("risk_per_trade_pct")) and 0 < r["risk_per_trade_pct"] <= 2, "risk.risk_per_trade_pct must be in (0,2]")
    _need(_num(r.get("daily_loss_limit_pct")) and 0 < r["daily_loss_limit_pct"] <= 10, "risk.daily_loss_limit_pct invalid")
    _need(isinstance(r.get("max_trades_per_day"), int) and r["max_trades_per_day"] > 0, "risk.max_trades_per_day invalid")
    _need(r.get("allow_averaging_down") is False and r.get("allow_stop_widening") is False, "averaging down / stop widening must be false")
    _need(_num(r.get("min_reward_risk_t1")) and r["min_reward_risk_t1"] >= 1.0, "risk.min_reward_risk_t1 must be >= 1")
    _need("POLICY_INVALID" in p["no_trade"] and "KILL_SWITCH" in p["no_trade"], "no_trade list must include POLICY_INVALID and KILL_SWITCH")
    return p


def load_policy(path: str | None = None) -> dict:
    try:
        with open(path or POLICY_PATH, encoding="utf-8") as fh:
            return validate(json.load(fh))
    except PolicyError:
        raise
    except Exception as e:                                  # unreadable / malformed JSON => fail closed
        raise PolicyError(f"policy could not be loaded: {type(e).__name__}: {e}") from e
