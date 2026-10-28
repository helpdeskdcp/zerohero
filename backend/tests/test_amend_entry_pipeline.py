"""AMEND_ENTRY through the real pipeline: ledger -> human_signals -> run -> settle. (2026-10-28: the entry price was dropped when events were read back, settlement raised KeyError and a participant's
orders could not be computed. The earlier tests fed events straight into settle() and missed it.)"""
import pytest

from app.arena import run as R, settle as S, submit as SU
from tests.test_arena import ep, snaps

BASE = {"participant": "DHANANJAY_DALVI", "underlying": "NIFTY", "expiry": "2026-11-03", "strike": 22600, "option_type": "CE", "entry_mode": "LIMIT", "entry": 95.0, "sl": 90.0, "targets": [120.0], "lots": 1}
SN = snaps([("10:01", 110, 109, 111), ("10:03", 104, 103, 105), ("10:05", 99, 98, 99.5), ("10:10", 125, 124, 126)])


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    p = str(tmp_path / "h.jsonl"); monkeypatch.setattr(SU, "PATH", p)
    monkeypatch.setattr(S, "load_snaps", lambda *a, **k: SN); monkeypatch.setattr(R.SM, "half_spread", lambda *a, **k: {"half_spread": 0.01, "source": "test"})
    return p


def run_all(p):
    sigs = R.human_signals(p, include_practice=True); out = R.run(sigs, market_db=None, cache={})
    return {x["id"]: x for v in out.values() if isinstance(v, dict) for x in v.get("results", [])}


def test_amended_entry_changes_the_fill_end_to_end(ledger):
    sig = SU.submit(BASE, path=ledger, now=ep("10:00"), practice=True)
    base = run_all(ledger)[sig["id"]]; assert base["status"] == "NOT_TRIGGERED"                       # LIMIT 95 never fills in this tape
    SU.submit_event({"signal_id": sig["id"], "action": "AMEND_ENTRY", "entry": 100.0}, "DHANANJAY_DALVI", path=ledger, now=ep("10:02"))
    r = run_all(ledger)[sig["id"]]
    assert r["status"] == "SETTLED" and r["fill"] == 99.5 and r["exit_reason"] == "TARGET"           # moved to 100 -> fills at the 10:05 ask


def test_the_event_keeps_its_entry_and_zone_when_read_back(ledger):
    sig = SU.submit(BASE, path=ledger, now=ep("10:00"), practice=True)
    SU.submit_event({"signal_id": sig["id"], "action": "AMEND_ENTRY", "entry": 101.5, "entry_hi": 103.0}, "DHANANJAY_DALVI", path=ledger, now=ep("10:02"))
    ev = R.human_signals(ledger, include_practice=True)[0]["events"]; assert ev[0]["action"] == "AMEND_ENTRY" and ev[0]["entry"] == 101.5 and ev[0]["entry_hi"] == 103.0


def test_a_malformed_amend_entry_event_is_ignored_not_fatal():
    sn = SN; sig = {"id": "x", "participant": "P", "underlying": "NIFTY", "strike": 22600, "option_type": "CE", "entry_mode": "LIMIT", "entry": 100.0, "entry_hi": 100.0, "sl": 90.0, "targets": [120.0], "t": ep("10:00"),
                    "events": [{"t": ep("10:02"), "action": "AMEND_ENTRY"}]}                          # no entry price at all
    r = S.settle(sig, sn, half_spread=0.01); assert r["status"] == "SETTLED" and r["fill"] == 99.5       # the original entry still applies


def test_one_bad_signal_cannot_take_down_everyone_elses_results(ledger, monkeypatch):
    a = SU.submit(BASE, path=ledger, now=ep("10:00"), practice=True); b = SU.submit({**BASE, "participant": "OTHER", "entry": 100.0}, path=ledger, now=ep("10:00"), practice=True)
    real = S.settle
    def flaky(sig, snaps_, **k):
        if sig["id"] == a["id"]: raise RuntimeError("boom")
        return real(sig, snaps_, **k)
    monkeypatch.setattr(S, "settle", flaky)
    out = run_all(ledger); assert out[a["id"]]["status"] == "NO_DATA:settlement_error:RuntimeError" and out[b["id"]]["status"] == "SETTLED"
