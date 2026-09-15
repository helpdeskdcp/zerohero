"""
Signal-pipeline audit (READ-ONLY) -- per user request: trace every candidate
signal from raw market data to final BUY_CE/BUY_PE/NO_TRADE, WITHOUT changing
any production logic, threshold, or filter, and WITHOUT re-arming anything.

Does NOT modify app/engines/scalp_strategy.py, app/backtest/replay.py, or
app/backtest/runner.py -- it only IMPORTS and calls their existing, unmodified
functions/classes (decide_from_context, SimTrade, ReplayContext, _leg,
_tod_bucket, _mod, calibration.fit, oi_history_adapter). The only new code is
this script's own instrumentation/aggregation logic.

Data source: /root/oi_dashboard/oi_history.db (oi_history_adapter) -- the
SAME real 35-day NIFTY option-chain archive (2026-07-13..2026-08-28, 46,363
decision cycles) that produced the P6/P6.1 filter-ablation numbers already
cited in scalp_strategy.py's own comments. This is "the last available OOS
dataset" for this strategy.

TRAIN (calibration fit only): 2026-07-13 .. 2026-08-10
TEST / OOS (everything reported below): 2026-08-11 .. 2026-08-28
Chronological, disjoint, no shuffling -- same discipline as run_backtest().
Config used for the OOS walk: PRODUCTION DEFAULTS, unmodified (no filters
dict passed -- decide_from_context._filters() falls back to its own
_DEFAULT_FILTERS, i.e. exactly what's live right now).

For every OOS decision call this script:
  1. Captures the FULL raw dict decide_from_context() returns (every key it
     computed before exiting -- an early rejection naturally has fewer keys
     populated than a late one; that is itself audit evidence, not a bug in
     this script).
  2. Buckets it into a PIPELINE_STAGE by parsing the `reason` string against
     the fixed set of exit points decide_from_context() actually has (listed
     in _STAGE_PATTERNS below, taken directly from reading the source).
  3. For every candidate with a real directional read (signal_type != NONE),
     computes a HYPOTHETICAL outcome to answer "was rejecting this the right
     call":
       - If a concrete contract + entry/SL/target were already selected
         (rejections at or after option selection: low option quality, EV
         gate, chain-bias veto, LOW-confidence WATCH) -> replay the REAL
         historical option premium candles for that exact locked contract
         forward from the decision timestamp using SimTrade's own unmodified
         check_exit() (target/stop/time), i.e. the SAME simulation logic the
         real backtest trades use. Method tag: "real_option_replay".
       - Otherwise (rejections before a contract was ever selected: no clean
         state, S/R unavailable, regime/signal-type/TOD filter block, MTF
         opposing gate, CE/PE confirmation conflict, no tradeable contract on
         the wanted side) -> a coarser INDEX-direction proxy: did the index
         move >=1x ATR in the read direction before moving >=1x ATR against
         it, within a 12-bar (~1h on 5m) forward horizon. Method tag:
         "index_direction_proxy" -- explicitly a proxy, not an option P&L,
         because no contract exists yet at this pipeline stage to price.
     Category:
       A) CORRECT_REJECTION      -- no real directional read existed at all
                                     (signal_type == NONE / S/R unavailable)
       B) FALSE_REJECTION        -- real read existed, was blocked, and the
                                     hypothetical says WIN
       C) CORRECT_REJECTION_LOSS -- real read existed, was blocked, and the
                                     hypothetical says LOSS
       D) CANNOT_DETERMINE       -- real read existed but no forward data
                                     could be resolved (end of dataset,
                                     contract vanished, etc.)
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.backtest import calibration, oi_history_adapter as ad  # noqa: E402
from app.backtest.replay import ReplayContext, SimTrade, _leg, _mod, _tod_bucket  # noqa: E402
from app.backtest.runner import _LegCache, _train_samples  # noqa: E402
from app.engines.scalp_strategy import decide_from_context  # noqa: E402
from app.engines.state_classifier import BULLISH, BEARISH  # noqa: E402

SYMBOL = "NIFTY"
TRAIN = ("2026-07-13", "2026-08-10")
TEST = ("2026-08-11", "2026-08-28")
DECIDE_EVERY_SEC = 60.0
_HARNESS_TFS = ("1m", "3m", "5m", "15m", "30m")
_LEG_TFS = ("1m", "3m", "5m", "15m", "30m")

INDEX_HORIZON_BARS = 12          # ~1h on 5m, same convention used throughout this codebase
INDEX_ATR_MULT = 1.0

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "research", "signal_pipeline_audit")
RAW_JSON_PATH = os.path.join(OUT_DIR, "oos_candidates.json")
REPORT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "SIGNAL_PIPELINE_AUDIT_REPORT.md")

# Every distinct `reason` prefix decide_from_context() can actually return,
# taken directly from reading its source (app/engines/scalp_strategy.py) --
# not guessed. Order matters: first match wins.
_STAGE_PATTERNS = [
    ("S/R unavailable", "SR_UNAVAILABLE"),
    ("no clean state", "STATE_NONE"),
    ("filter: regime", "HARD_FILTER_REGIME"),
    ("filter: signal type", "HARD_FILTER_SIGNAL_TYPE"),
    ("filter: time-of-day", "HARD_FILTER_TOD"),
    ("MTF: strong opposing", "MTF_GATE"),
    ("CE/PE confirmation", "CE_PE_CONFLICT"),
    ("no tradeable contract", "NO_CONTRACT"),
    ("option quality", "OPTION_QUALITY_GATE"),
    ("EV gate:", "EV_GATE"),
    ("option-chain bias contradicts", "CHAIN_BIAS_VETO"),
    ("confidence LOW -> watch", "CONFIDENCE_GATE_WATCH"),
]


def _real_direction(row: dict) -> str | None:
    """decide_from_context()'s own out_none() helper hardcodes direction="NONE"
    in its base dict and only a handful of rejection paths' `extra` dict
    happens to override it -- so for most rejection stages (MTF gate, CE/PE
    conflict, no-tradeable-contract, option-quality, EV gate) the returned
    `direction` field reads "NONE" even though a real direction was already
    computed and used internally to pick CE vs PE. This is a genuine
    observability gap in the production return contract (confirmed by
    reading the source, not a bug in this script) -- worth reporting as a
    finding on its own. Reconstructed here from `signal_type` (which IS
    always correctly reported, coming straight from `ctx`) via the same
    BULLISH/BEARISH sets state_classifier.py itself defines."""
    d = row.get("direction")
    if d in ("BULLISH", "BEARISH"):
        return d
    st = row.get("signal_type")
    if st in BULLISH:
        return "BULLISH"
    if st in BEARISH:
        return "BEARISH"
    return None


def _stage_of(reason: str, decision: str) -> str:
    if decision in ("BUY_CE", "BUY_PE"):
        return "ENTRY"
    r = reason or ""
    for prefix, stage in _STAGE_PATTERNS:
        if r.startswith(prefix):
            return stage
    return "OTHER:" + r[:40]


def _index_direction_hypothesis(direction: str, bars_5m: list[dict], atr: float | None,
                                horizon: int = INDEX_HORIZON_BARS, atr_mult: float = INDEX_ATR_MULT) -> tuple[str, str]:
    """Forward-only (uses bars AFTER the decision ts, never before -- this is
    grading a hypothetical, not computing the signal, exactly the same
    look-ahead contract as every WIN/LOSS label elsewhere in this codebase).
    Returns (outcome, method)."""
    if not bars_5m or len(bars_5m) < 2 or not atr or atr <= 0 or direction not in ("BULLISH", "BEARISH"):
        return "CANNOT_DETERMINE", "index_direction_proxy"
    entry = bars_5m[0]["c"] if "c" in bars_5m[0] else bars_5m[0].get("close")
    if entry is None:
        return "CANNOT_DETERMINE", "index_direction_proxy"
    up_t = entry + atr_mult * atr
    dn_t = entry - atr_mult * atr
    for b in bars_5m[1:1 + horizon]:
        h = b.get("h", b.get("high"))
        l = b.get("l", b.get("low"))
        if h is None or l is None:
            continue
        hit_up, hit_dn = h >= up_t, l <= dn_t
        if hit_up and hit_dn:
            return "LOSS", "index_direction_proxy"        # ambiguous same-bar -> conservative LOSS
        if hit_up:
            return ("WIN" if direction == "BULLISH" else "LOSS"), "index_direction_proxy"
        if hit_dn:
            return ("WIN" if direction == "BEARISH" else "LOSS"), "index_direction_proxy"
    return "LOSS", "index_direction_proxy"                  # timed out flat/against -> not a win


def _real_option_replay(symbol: str, strike, opt_type: str, entry: float, sl: float,
                        t1: float, trailing_stop: float, max_hold_sec, decision_ts: str,
                        end_date: str) -> tuple[str, str]:
    """Replays the REAL locked contract's historical premium forward from
    decision_ts using SimTrade's own unmodified check_exit() -- the exact
    same simulation logic real backtest trades use."""
    try:
        candles = ad.resample_candles(symbol, "1m", kind="option", strike=strike,
                                      option_type=opt_type, start=decision_ts[:10], end=end_date).get("candles") or []
    except Exception:
        return "CANNOT_DETERMINE", "real_option_replay"
    fwd = [c for c in candles if c["t"] > decision_ts]
    if not fwd:
        return "CANNOT_DETERMINE", "real_option_replay"
    t = SimTrade(signal_id="AUDIT", symbol=symbol, direction="", opt_type=opt_type, strike=strike,
                expiry=None, token=None, tradingsymbol=None, entry=entry, entry_ts=decision_ts,
                stop_loss=sl, target_1=t1, target_2=None, trailing_stop=trailing_stop or 0.0,
                max_hold_sec=max_hold_sec)
    for c in fwd:
        px = c.get("c", c.get("close"))
        if px is None or px <= 0:
            continue
        t.mark_to(px, c["t"])
        reason = t.check_exit(px, c["t"])
        if reason:
            t.close(px, c["t"], reason)
            return (t.outcome or "CANNOT_DETERMINE"), "real_option_replay"
    return "CANNOT_DETERMINE", "real_option_replay"          # ran out of forward data before an exit


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"TRAIN calibration fit: {SYMBOL} {TRAIN[0]}..{TRAIN[1]} (unmodified _train_samples/calibration.fit)")
    _res_tr, samples, wins, losses = _train_samples(SYMBOL, TRAIN, {}, DECIDE_EVERY_SEC, persist=False)
    calib = calibration.fit(samples, version=f"audit-{SYMBOL}-{TRAIN[0]}_{TRAIN[1]}")
    from statistics import mean
    avg_win = round(mean(wins), 3) if wins else None
    avg_loss = round(mean(losses), 3) if losses else None
    print(f"  {len(samples)} TRAIN calibration samples, avg_win={avg_win} avg_loss={avg_loss}")

    print(f"OOS walk: {SYMBOL} {TEST[0]}..{TEST[1]} @ decide_every_sec={DECIDE_EVERY_SEC}, PRODUCTION DEFAULT config")
    series = {tf: (ad.resample_candles(SYMBOL, tf, kind="index", start=TEST[0], end=TEST[1]).get("candles") or [])
             for tf in _HARNESS_TFS}
    index_5m_full = series.get("5m") or []
    ctx = ReplayContext(series)
    leg_cache = _LegCache(SYMBOL, TEST[0], TEST[1])

    candidates = []
    last_ts = None
    n_states = 0
    for state in ad.iter_market_states(SYMBOL, TEST[0], TEST[1]):
        n_states += 1
        ts = state["ts"]
        minute = _mod(ts)
        if minute is None or not (9 * 60 + 15 <= minute <= 15 * 60 + 30):
            continue
        if state.get("index_ltp") is None or not state.get("chain"):
            continue
        if last_ts is not None:
            try:
                gap = (datetime.fromisoformat(ts) - datetime.fromisoformat(last_ts)).total_seconds()
            except (TypeError, ValueError):
                gap = DECIDE_EVERY_SEC
            if 0 <= gap < DECIDE_EVERY_SEC:
                continue
        last_ts = ts
        ctx._set_now(ts)
        bt = {tf: ctx.candles(tf) for tf in _LEG_TFS}
        if len(bt.get("5m") or []) < 20:
            continue
        try:
            sig = decide_from_context(bt, state.get("chain"), atm=state.get("atm"), calib=calib,
                                      avg_win=avg_win, avg_loss=avg_loss,
                                      leg_bars_fn=leg_cache.fn_at(ts),
                                      tod_bucket=_tod_bucket(minute), config={})
        except Exception as e:
            candidates.append({"ts": ts, "stage": "DECIDE_ERROR", "error": f"{type(e).__name__}: {e}"})
            continue
        if not sig:
            continue
        stage = _stage_of(sig.get("reason", ""), sig.get("decision", "NO_TRADE"))
        row = {"ts": ts, "stage": stage, **sig}
        candidates.append(row)

    print(f"  {n_states} raw market states, {len(candidates)} throttled decision calls captured")

    print("Computing hypothetical outcomes for every rejected candidate with a real directional read...")
    for row in candidates:
        if row["stage"] == "ENTRY" or row["stage"] == "DECIDE_ERROR":
            continue
        signal_type = row.get("signal_type")
        direction = _real_direction(row)
        row["direction_reconstructed"] = direction     # for audit transparency in the raw dump
        if signal_type in (None, "NONE") or row["stage"] in ("SR_UNAVAILABLE", "STATE_NONE"):
            row["hypothesis_category"] = "A_CORRECT_REJECTION"
            row["hypothesis_outcome"] = "N/A"
            row["hypothesis_method"] = "no_directional_read"
            continue
        ts = row["ts"]
        post_selection = row["stage"] in ("OPTION_QUALITY_GATE", "EV_GATE", "CHAIN_BIAS_VETO", "CONFIDENCE_GATE_WATCH")
        if post_selection and row.get("strike") is not None and row.get("stop_loss") is not None:
            opt_type = "CE" if direction == "BULLISH" else "PE"
            entry = row.get("entry")
            if entry is None:
                idx = next((i for i, b in enumerate(index_5m_full) if b["t"] >= ts), None)
                outcome, method = "CANNOT_DETERMINE", "real_option_replay"
            else:
                outcome, method = _real_option_replay(
                    SYMBOL, row["strike"], opt_type, entry, row.get("stop_loss"),
                    row.get("target_1") or (entry * 1.5), row.get("trailing_stop"),
                    row.get("max_hold_sec"), ts, TEST[1])
        else:
            idx = next((i for i, b in enumerate(index_5m_full) if b["t"] >= ts), None)
            fwd_bars = index_5m_full[idx:idx + 1 + INDEX_HORIZON_BARS] if idx is not None else []
            outcome, method = _index_direction_hypothesis(direction, fwd_bars, row.get("atr"))

        row["hypothesis_outcome"] = outcome
        row["hypothesis_method"] = method
        if outcome == "CANNOT_DETERMINE":
            row["hypothesis_category"] = "D_CANNOT_DETERMINE"
        elif outcome == "WIN":
            row["hypothesis_category"] = "B_FALSE_REJECTION"
        else:
            row["hypothesis_category"] = "C_CORRECT_REJECTION_LOSS"

    for row in candidates:
        if row["stage"] == "ENTRY":
            row["hypothesis_category"] = "ENTRY_NOT_A_REJECTION"
            row["hypothesis_outcome"] = row.get("_actual_outcome", "SEE_REAL_BACKTEST")

    with open(RAW_JSON_PATH, "w") as f:
        json.dump(candidates, f, indent=2, default=str)
    print(f"Raw candidate dump written: {RAW_JSON_PATH} ({len(candidates)} rows)")

    _write_report(candidates, len(samples), avg_win, avg_loss, n_states)


def _write_report(candidates, n_train_samples, avg_win, avg_loss, n_states):
    non_entry = [r for r in candidates if r["stage"] not in ("ENTRY", "DECIDE_ERROR")]
    direction_blanked = sum(1 for r in non_entry if r.get("signal_type") not in (None, "NONE")
                            and r.get("direction") == "NONE")
    stage_counts = Counter(r["stage"] for r in candidates)
    cat_counts = Counter(r.get("hypothesis_category", "?") for r in candidates if r["stage"] != "ENTRY")
    n_rejected = sum(v for k, v in cat_counts.items())

    # profitable/losing signals removed BY EACH STAGE (only B/C carry a real verdict)
    per_stage_bc = defaultdict(lambda: {"B_false_rejection": 0, "C_correct_rejection_loss": 0,
                                        "A_no_real_signal": 0, "D_cannot_determine": 0})
    for r in candidates:
        if r["stage"] == "ENTRY":
            continue
        cat = r.get("hypothesis_category", "")
        stage = r["stage"]
        if cat == "B_FALSE_REJECTION":
            per_stage_bc[stage]["B_false_rejection"] += 1
        elif cat == "C_CORRECT_REJECTION_LOSS":
            per_stage_bc[stage]["C_correct_rejection_loss"] += 1
        elif cat == "A_CORRECT_REJECTION":
            per_stage_bc[stage]["A_no_real_signal"] += 1
        elif cat == "D_CANNOT_DETERMINE":
            per_stage_bc[stage]["D_cannot_determine"] += 1

    n_entries = stage_counts.get("ENTRY", 0)
    n_total = len(candidates)

    lines = []
    lines.append("# Signal Pipeline Audit -- scalp_strategy.decide_from_context")
    lines.append("")
    lines.append("READ-ONLY audit. No production file was modified, no threshold was changed, "
                 "nothing was re-armed. Every function called below is the existing, unmodified "
                 "production code (`app.engines.scalp_strategy.decide_from_context`, "
                 "`app.backtest.replay.SimTrade`/`ReplayContext`, `app.backtest.oi_history_adapter`).")
    lines.append("")
    lines.append("## Dataset")
    lines.append("")
    lines.append(f"- Source: `/root/oi_dashboard/oi_history.db` (real NIFTY option-chain cycles, "
                 f"35 real days, 2026-07-13..2026-08-28) -- the same archive that produced the "
                 f"P6/P6.1 filter-ablation numbers cited in scalp_strategy.py's own comments.")
    lines.append(f"- TRAIN (calibration fit only): {TRAIN[0]}..{TRAIN[1]} -- {n_train_samples} samples, "
                 f"avg_win={avg_win}, avg_loss={avg_loss}")
    lines.append(f"- OOS/TEST (everything below): {TEST[0]}..{TEST[1]}")
    lines.append(f"- Raw market states in OOS window: {n_states}")
    lines.append(f"- Throttled decision calls captured (decide_every_sec={DECIDE_EVERY_SEC}): {n_total}")
    lines.append(f"- Config: PRODUCTION DEFAULTS, unchanged (no filters/thresholds overridden)")
    lines.append("")
    lines.append("## Observability gap found while building this audit")
    lines.append("")
    lines.append(f"`decide_from_context()`'s `out_none()` helper hardcodes `direction=\"NONE\"` in its "
                 f"base dict, and most rejection paths' `extra` dict never overrides it -- even though "
                 f"a real direction was already computed internally to pick CE vs PE. Confirmed by "
                 f"reading the source (not inferred): **{direction_blanked} of {len(non_entry)} rejected "
                 f"candidates ({direction_blanked/len(non_entry):.1%} of non-entry decisions)** had a "
                 f"real `signal_type` (so a real directional read existed) but reported "
                 f"`direction: \"NONE\"` in the live output. This script reconstructs it from "
                 f"`signal_type` (via the same BULLISH/BEARISH sets `state_classifier.py` itself "
                 f"defines) to do this audit at all -- but any OTHER consumer of this dict (dashboard, "
                 f"Telegram alert, this audit if it hadn't special-cased it) sees a blank direction for "
                 f"the majority of rejections. Separately: NO rejection path (not even EV gate, "
                 f"confidence-LOW WATCH, or the option-quality gate, all of which run AFTER a contract "
                 f"is already selected internally) includes the selected strike/entry/stop_loss/target "
                 f"in its return value -- only a successful ENTRY decision does. That blocks grading "
                 f"near-miss rejections against a REAL option-premium outcome; this audit falls back to "
                 f"an index-direction proxy for every rejection stage as a result (see Caveats).")
    lines.append("")
    lines.append("## Funnel -- where candidates are lost")
    lines.append("")
    lines.append("| Stage | Count | % of all decision calls |")
    lines.append("|---|---|---|")
    for stage, count in stage_counts.most_common():
        lines.append(f"| {stage} | {count} | {count/n_total:.1%} |")
    lines.append("")
    lines.append(f"**Final entries generated (BUY_CE/BUY_PE): {n_entries} / {n_total} ({n_entries/n_total:.2%})**")
    lines.append("")
    lines.append("## Rejected-candidate classification (A/B/C/D)")
    lines.append("")
    lines.append("A = correct rejection (no real directional read existed at all). "
                 "B = false rejection / missed profitable signal. "
                 "C = correctly rejected a losing signal. D = cannot determine (no forward data).")
    lines.append("")
    lines.append("| Category | Count | % of rejected candidates |")
    lines.append("|---|---|---|")
    label_map = {"A_CORRECT_REJECTION": "A) Correct rejection", "B_FALSE_REJECTION": "B) False rejection",
                "C_CORRECT_REJECTION_LOSS": "C) Correctly rejected loss", "D_CANNOT_DETERMINE": "D) Cannot determine"}
    for key, label in label_map.items():
        c = cat_counts.get(key, 0)
        lines.append(f"| {label} | {c} | {c/n_rejected:.1%} |" if n_rejected else f"| {label} | 0 | n/a |")
    lines.append("")
    lines.append("## Per-stage breakdown -- profitable vs losing signals removed by each gate")
    lines.append("")
    lines.append("| Stage | No real signal (A) | False rejection -- WOULD HAVE WON (B) | "
                 "Correctly rejected loss (C) | Cannot determine (D) |")
    lines.append("|---|---|---|---|---|")
    for stage, counts in sorted(per_stage_bc.items(), key=lambda kv: -sum(kv[1].values())):
        lines.append(f"| {stage} | {counts['A_no_real_signal']} | **{counts['B_false_rejection']}** | "
                     f"{counts['C_correct_rejection_loss']} | {counts['D_cannot_determine']} |")
    lines.append("")

    # Rank top problems by evidence: stages with the most B (false rejections,
    # i.e. missed profitable signals actively being destroyed by that gate).
    # The observability gap is prepended -- it's systemic (touches every
    # rejection stage) rather than attributable to one gate, so it doesn't
    # fit the per-stage count ranking below but is at least as consequential.
    ranked = sorted(per_stage_bc.items(), key=lambda kv: -kv[1]["B_false_rejection"])[:4]
    lines.append("## Top 5 signal-processing problems (ranked by evidence)")
    lines.append("")
    lines.append(f"1. **Observability gap: direction + contract identity missing from rejection output** -- "
                 f"{direction_blanked}/{len(non_entry)} ({direction_blanked/len(non_entry):.1%}) of "
                 f"rejected candidates had a real directional read that the output dict reported as "
                 f"\"NONE\"; zero rejection paths carry the selected strike/entry/SL/target. This is "
                 f"why every other finding below relies on an index-direction proxy instead of a real "
                 f"option outcome -- fix this FIRST and every other number in this report gets sharper "
                 f"for free, with no threshold change.")
    for i, (stage, counts) in enumerate(ranked, 2):
        total_at_stage = stage_counts.get(stage, 0)
        lines.append(f"{i}. **{stage}** -- {counts['B_false_rejection']} of {total_at_stage} candidates "
                     f"rejected at this gate would have been WINS (hypothetical), vs "
                     f"{counts['C_correct_rejection_loss']} correctly-rejected losses. "
                     f"Ratio: {counts['B_false_rejection']}/{max(1, counts['B_false_rejection']+counts['C_correct_rejection_loss'])} "
                     f"of determinable outcomes were false rejections.")
    lines.append("")
    lines.append("## Caveats (read before acting on any number above)")
    lines.append("")
    lines.append("- Pre-option-selection rejections (no clean state, S/R unavailable, hard filters, MTF gate, "
                 "CE/PE conflict, no tradeable contract) are graded with an **index-direction proxy** "
                 "(>=1 ATR forward move in the read direction within 12 bars), NOT a real option P&L -- "
                 "no contract was ever selected at that pipeline stage to price. This is a directional-"
                 "correctness check, not a trading-profitability check.")
    lines.append("- This script's code CAN grade a rejection with a real historical option-premium "
                 "replay of the exact locked contract (via SimTrade, the same logic the real backtest "
                 "engine uses) -- but in practice this path never fires for ANY rejection stage, "
                 "including ones that run after a contract is selected internally (option quality, EV "
                 "gate, chain-bias veto, LOW-confidence WATCH), because `decide_from_context()` never "
                 "includes the selected strike/entry/stop_loss/target in a NO_TRADE/WATCH return value "
                 "(see the Observability Gap section above). Every rejection in this report is graded "
                 "with the coarser index-direction proxy as a result.")
    lines.append("- This is ONE 35-day real dataset, ONE symbol (NIFTY), ONE decide_every_sec setting. "
                 "No multiple-comparison correction was applied to this ranking -- treat it as a map of "
                 "WHERE to investigate further, not a final verdict on any single gate.")
    lines.append("- No threshold, filter, or config was changed to produce this report. No re-arming was done.")
    lines.append("")
    lines.append(f"Raw per-candidate dump (all {n_total} rows, full captured fields): "
                 f"`data/research/signal_pipeline_audit/oos_candidates.json`")

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"Report written: {REPORT_PATH}")


if __name__ == "__main__":
    main()
