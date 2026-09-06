"""
H1/H7 Structural State Engine -- chronological replay + research parity.

  * replay: on every real captured session, re-classifying an event with the
    bar array truncated to (event_bar + 3) must give the identical state /
    action -- proof there is zero look-ahead on real data.
  * parity: where our engine emits an event at the same (symbol, timestamp) as
    a row in the frozen Stage-7 research CSV, the H7 reclaim-distance band must
    agree with the CSV's reclaim_dist_spk (the boundary is not re-derived here).

Both are skipped when the underlying data is not present in this environment.
"""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import market_hub                      # noqa: E402
from app.orderflow import h1h7_state as H       # noqa: E402

SYMBOLS = ["CRUDEOIL", "NIFTY", "NATURALGAS"]
STAGE7_CSV = Path(__file__).parents[1] / "data" / "orderflow_stage7_events.csv"


def _sessions(sym, limit=8):
    try:
        return market_hub.session_dates(sym, limit=limit)
    except Exception:
        return []


def _bar_index(bars, ts):
    for i, b in enumerate(bars):
        if b.get("bar_start") == ts:
            return i
    return None


# ================================================================ replay: no look-ahead on real data
@pytest.mark.parametrize("sym", SYMBOLS)
def test_replay_is_causal_on_every_real_session(sym):
    dates = _sessions(sym)
    if not dates:
        pytest.skip(f"no captured sessions for {sym}")
    checked = 0
    for d in dates:
        bars = market_hub.session_bars(sym, d)
        if len(bars) < 20:
            continue
        full = H.classify_session(bars, sym)
        for e in full["events"]:
            idx = _bar_index(bars, e["timestamp"])
            assert idx is not None
            # the engine may use bars idx+1..idx+3 only
            trunc = H.classify_session(bars[:idx + 4], sym)
            te = [x for x in trunc["events"] if x["timestamp"] == e["timestamp"]]
            assert len(te) == 1, f"{sym} {d} {e['timestamp']} vanished on truncation"
            assert te[0]["state"] == e["state"], (
                f"{sym} {d} {e['timestamp']}: {te[0]['state']} != {e['state']} (look-ahead!)")
            assert te[0]["action"] == e["action"]
            assert te[0] == e
            checked += 1
    if checked == 0:
        pytest.skip(f"no eligible events for {sym}")


@pytest.mark.parametrize("sym", SYMBOLS)
def test_replay_states_and_actions_are_all_in_the_allowed_set(sym):
    dates = _sessions(sym)
    if not dates:
        pytest.skip(f"no captured sessions for {sym}")
    seen_states = set()
    for d in dates:
        res = H.classify_session(market_hub.session_bars(sym, d), sym)
        assert res["live_trading"] is False
        for e in res["events"]:
            assert e["action"] in {"AVOID", "NO_ACTION", "CONTINUATION_CANDIDATE"}
            assert e["state"] in H._ACTION
            assert H._ACTION[e["state"]] == e["action"]
            seen_states.add(e["state"])
            # continuation candidate only ever for CRUDEOIL
            if e["state"] == "H1_CONT":
                assert e["symbol"] == "CRUDEOIL"
            if e["state"] == "H1_CONT_OBSERVE":
                assert e["symbol"] in ("NIFTY", "NATURALGAS")
    # research status never claims PROVEN
    assert "PROVEN" not in seen_states


# ================================================================ parity with the frozen Stage-7 CSV
def _load_stage7():
    if not STAGE7_CSV.exists():
        return []
    with STAGE7_CSV.open(newline="") as f:
        return list(csv.DictReader(f))


def test_h7_band_parity_with_stage7_events_csv():
    rows = _load_stage7()
    if not rows:
        pytest.skip("data/orderflow_stage7_events.csv not present")

    # index our engine's events by (symbol, timestamp)
    ours = {}
    for sym in SYMBOLS:
        for d in _sessions(sym, limit=40):
            for e in H.classify_session(market_hub.session_bars(sym, d), sym)["events"]:
                ours[(sym, e["timestamp"])] = e
    if not ours:
        pytest.skip("engine produced no events (no captured bars)")

    compared = agree = 0
    mism = []
    for r in rows:
        key = (r.get("symbol"), r.get("timestamp"))
        e = ours.get(key)
        if e is None:
            continue
        try:
            reclaimed = str(r.get("reclaim2", "")).strip().lower() in ("true", "1")
            ratio = float(r.get("reclaim_dist_spk"))
        except (TypeError, ValueError):
            continue
        if not reclaimed:
            continue
        compared += 1
        if ratio > H.RECLAIM_KNEE:
            want = {"H7_TRAP"}
        elif ratio > H.RECLAIM_SHALLOW:
            want = {"H7_TRAP", "H7_LEANING_TRAP"}
        else:
            want = {"AMBIGUOUS", "H7_LEANING_TRAP"}
        if e["state"] in want:
            agree += 1
        else:
            mism.append((key, round(ratio, 3), e["state"], sorted(want)))

    if compared == 0:
        pytest.skip("no overlapping reclaim events between engine and Stage-7 CSV")
    frac = agree / compared
    assert frac >= 0.85, (
        f"H7 band parity {agree}/{compared} = {frac:.2f} < 0.85; sample mismatches: {mism[:8]}")


def test_stage7_h7_label_rows_are_never_continuation_candidates():
    """A row the frozen research flagged H7 (trap) must never come back from the
    shadow engine as a CONTINUATION_CANDIDATE."""
    rows = _load_stage7()
    if not rows:
        pytest.skip("data/orderflow_stage7_events.csv not present")
    ours = {}
    for sym in SYMBOLS:
        for d in _sessions(sym, limit=40):
            for e in H.classify_session(market_hub.session_bars(sym, d), sym)["events"]:
                ours[(sym, e["timestamp"])] = e
    hits = 0
    for r in rows:
        if str(r.get("H7", "")).strip().lower() not in ("true", "1"):
            continue
        e = ours.get((r.get("symbol"), r.get("timestamp")))
        if e is None:
            continue
        hits += 1
        assert e["action"] != "CONTINUATION_CANDIDATE", (
            f"Stage-7 H7 row {r.get('symbol')} {r.get('timestamp')} -> {e['state']}")
    if hits == 0:
        pytest.skip("no overlapping Stage-7 H7 rows with captured bars")
