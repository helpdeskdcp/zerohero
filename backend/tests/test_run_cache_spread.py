"""run.run with a result cache: a signal answered from the cache must not leave a spread placeholder (half_spread=None) behind for the next, still open, signal of the same underlying."""
import pytest

from app.arena import run as R, settle as S, submit as SU
from tests.test_arena import ep, snaps

BASE = {"participant": "DHANANJAY_DALVI", "underlying": "NIFTY", "expiry": "2026-11-03", "strike": 22600, "option_type": "CE", "entry_mode": "NEAR", "entry": 111.0, "sl": 100.0, "targets": [120.0], "lots": 1}
SN = snaps([("10:01", 110, 109, 111), ("10:03", 104, 103, 105), ("10:05", 99, 99, 99), ("10:10", 125, 125, 125)])             # 10:05 and 10:10 are LOCKED quotes (bid == ask): the price falls back to ltp +- the half-spread


def test_open_signal_after_a_cached_final_one_of_the_same_underlying_is_settled_normally(tmp_path, monkeypatch):
    p = str(tmp_path / "h.jsonl"); monkeypatch.setattr(SU, "PATH", p); calls = []
    monkeypatch.setattr(S, "load_snaps", lambda *a, **k: SN); monkeypatch.setattr(R.SM, "half_spread", lambda *a, **k: (calls.append(1), {"half_spread": 0.01, "source": "test"})[1])
    a = SU.submit(BASE, path=p, now=ep("10:00"), practice=True)
    cache = {}; out = R.run(R.human_signals(p, include_practice=True), market_db=None, cache=cache)
    assert out["DHANANJAY_DALVI"]["results"][0]["status"] == "SETTLED" and any(isinstance(k, tuple) and len(k) == 2 for k in cache)          # first run: A is final and cached
    b = SU.submit({**BASE, "entry_mode": "LIMIT", "entry": 100.0, "sl": 90.0, "targets": [120.0]}, path=p, now=ep("10:00") + 30, practice=True)                                      # B: a LIMIT that never fills -> NOT_TRIGGERED, recomputed on every refresh
    out = R.run(R.human_signals(p, include_practice=True), market_db=None, cache=cache)
    res = {x["id"]: x for x in out["DHANANJAY_DALVI"]["results"]}
    assert res[a["id"]]["status"] == "SETTLED" and res[b["id"]]["status"] != "NO_DATA:settlement_error:TypeError" and not str(res[b["id"]]["status"]).startswith("NO_DATA:settlement_error"), res[b["id"]]["status"]
    assert res[b["id"]]["status"] == "SETTLED" and res[b["id"]]["spread_source"] == "test"                  # B filled on the locked quote with the measured half-spread
    assert out["_spread_models"]["NIFTY"]["half_spread"] == 0.01
