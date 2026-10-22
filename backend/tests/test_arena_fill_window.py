import time

import pytest

from app.arena import channel_ingest as CI, run as R, settle as S, submit as SU
from tests.test_channel_ingest import post
from tests.test_channel_parse import HERO


def snaps(t0, rows):
    return [{"t": t0 + dt, "ltp": ask, "bid": ask - 1, "ask": ask} for dt, ask in rows]


def sig(t0, **kw):
    return {"id": "X", "participant": "P", "underlying": "NIFTY", "expiry": "2026-10-27", "strike": 23000.0, "option_type": "PE", "entry_mode": "LIMIT", "entry": 100.0, "entry_hi": 100.0, "sl": 60.0, "targets": [140.0], "t": t0, "events": [], **kw}


def test_limit_expires_when_not_filled_inside_its_window():
    t0 = 1_760_000_000.0                                     # an arbitrary instant; the session-end logic only needs the snapshots to stay within one session
    rows = [(30, 120), (400, 118), (1000, 99), (1100, 150)]
    late = S.settle(sig(t0, valid_sec=900), snaps(t0, rows), half_spread=0.0); assert late["status"] == "NOT_TRIGGERED"             # the 99 ask comes at +1000 s: too late
    old = S.settle(sig(t0), snaps(t0, rows), half_spread=0.0); assert old["status"] != "NOT_TRIGGERED" and old["fill"] == 99.0        # without a window the old (session-long) behaviour is unchanged
    ok = S.settle(sig(t0, valid_sec=900), snaps(t0, [(30, 120), (400, 99), (500, 150)]), half_spread=0.0); assert ok["fill"] == 99.0 and ok["status"] != "NOT_TRIGGERED"   # filled inside the window


def test_validate_and_ledger_keep_valid_sec(tmp_path):
    base = {"participant": "P", "underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "LIMIT", "entry": 100, "sl": 80, "targets": [120]}
    assert "valid_sec" not in SU.validate(base) and SU.validate({**base, "valid_sec": 900})["valid_sec"] == 900
    for bad in (30, 99999): 
        with pytest.raises(SU.SubmissionError): SU.validate({**base, "valid_sec": bad})
    rec = SU.submit({**base, "valid_sec": 900}, path=str(tmp_path / "l.jsonl"), practice=True); assert rec["valid_sec"] == 900
    assert R.human_signals(str(tmp_path / "l.jsonl"), include_practice=True)[0]["valid_sec"] == 900


def test_channel_ingested_entries_carry_the_15_minute_window(tmp_path, monkeypatch):
    monkeypatch.setattr(CI, "pick_expiry", lambda u, d, p=None: "2026-10-27"); monkeypatch.setattr(CI.CT, "check", lambda *a, **k: {"problems": [], "warnings": [], "last": None}); monkeypatch.setattr(CI, "_market_open", lambda u: True)
    kw = dict(participant="INSTITUTION_TRADER", mode_fn=lambda: "ARENA_LIVE", ledger=str(tmp_path / "l.jsonl"), log=str(tmp_path / "log.jsonl"))
    r = CI.handle_post(post(1, HERO), {"seen": {}, "by_msg": {}, "last_by_chat": {}}, **kw); assert r["outcome"] == "SUBMITTED"
    assert SU.load(kw["ledger"])[0]["valid_sec"] == 900


def test_validity_correction_only_for_channel_signals_and_is_applied(tmp_path):
    led = str(tmp_path / "l.jsonl"); base = {"participant": "P", "underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "LIMIT", "entry": 100, "sl": 80, "targets": [120]}
    ch = SU.submit({**base, "note": "via channel post 5"}, path=led, practice=True); manual = SU.submit(base, path=led, practice=True); win = SU.submit({**base, "valid_sec": 600, "note": "via channel post 6"}, path=led, practice=True)
    SU.submit_validity_correction(ch["id"], 900, "rehearsal re-score", path=led)
    for bad in (manual["id"], win["id"]):
        with pytest.raises(SU.SubmissionError): SU.submit_validity_correction(bad, 900, "x", path=led)
    got = {s["id"]: s.get("valid_sec") for s in R.human_signals(led, include_practice=True)}
    assert got == {ch["id"]: 900, manual["id"]: None, win["id"]: 600}


def test_run_cache_reuses_final_results_and_resettles_open_ones(monkeypatch):
    calls = []
    def fake_settle(sig, snaps, *, half_spread, policy="T1", latency=20):
        calls.append(sig["id"]); done = sig["id"] != "OPEN"
        return {"id": sig["id"], "participant": sig["participant"], "status": "SETTLED" if done else "NO_DATA:no_exit_snapshot", "t": sig["t"], **({"net_rs": 1.0, "gross_rs": 2.0, "cost_rs": 1.0, "fill_t": sig["t"], "fill_real_quote": True, "exit_real_quote": True} if done else {})}
    monkeypatch.setattr(R.S, "settle", fake_settle); monkeypatch.setattr(R.S, "load_snaps", lambda *a, **k: []); monkeypatch.setattr(R.SM, "half_spread", lambda *a, **k: {"half_spread": 0.0, "source": "TEST"})
    sg = lambda i: {"id": i, "participant": "P", "underlying": "NIFTY", "expiry": "2026-10-27", "strike": 1.0, "option_type": "CE", "t": 1_760_000_000.0}
    cache = {}; R.run([sg("A"), sg("OPEN")], cache=cache); assert calls == ["A", "OPEN"]
    R.run([sg("A"), sg("OPEN")], cache=cache); assert calls == ["A", "OPEN", "OPEN"]                                     # the settled one came from the cache, the open one was settled again
    R.run([{**sg("A"), "sl": 1.0}], cache=cache); assert calls[-1] == "A"                                                  # any change to the signal's content invalidates its entry
    R.run([sg("A")]); assert calls.count("A") == 3                                                                          # no cache argument: old behaviour
