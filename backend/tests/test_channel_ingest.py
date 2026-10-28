import json
import time

import pytest

from app.arena import channel_ingest as CI
from app.arena import submit as SU
from tests.test_channel_parse import HERO

from datetime import datetime, timedelta, timezone
FIXED_NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))).timestamp()          # the fixtures name the 27-Oct-2026 expiry: fix 'today' so they do not rot with the calendar

MCX = "#NATURALGAS _290 PE\nBuy Range : 9-10\nTarget : 12/14\nStoploss : 7"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(CI, "pick_expiry", lambda u, d, p=None: "2026-10-27")
    monkeypatch.setattr(CI.CT, "check", lambda *a, **k: {"problems": [], "warnings": [], "last": None})
    monkeypatch.setattr(CI, "_market_open", lambda u: True)
    st = {"seen": {}, "by_msg": {}, "last_by_chat": {}}
    return dict(st=st, kw=dict(participant="INSTITUTION_TRADER", mode_fn=lambda: "ARENA_LIVE", ledger=str(tmp_path / "l.jsonl"), log=str(tmp_path / "log.jsonl")), tmp=tmp_path)


def post(mid, text, reply=None):
    p = {"chat": {"id": -100123, "type": "channel"}, "message_id": mid, "date": int(time.time()) - 30, "text": text}
    if reply: p["reply_to_message"] = {"message_id": reply}
    return p


def test_submit_dedupe_and_cancel(env):
    r = CI.handle_post(post(1, HERO), env["st"], **env["kw"]); assert r["outcome"] == "SUBMITTED" and r["signal_id"] == "H00001"
    rec = SU.load(env["kw"]["ledger"])[0]; assert (rec["underlying"], rec["strike"], rec["entry_mode"], rec["entry"], rec["sl"], rec["expiry"], rec["practice"]) == ("NIFTY", 22400.0, "LIMIT", 80.0, 5.0, "2026-10-27", False)
    assert CI.handle_post(post(1, HERO), env["st"], **env["kw"])["outcome"] == "DUPLICATE" and len(SU.load(env["kw"]["ledger"])) == 1
    assert CI.handle_post(post(2, "Target 1 achieved"), env["st"], **env["kw"])["outcome"] == "CLAIM_RECORDED"
    assert len(SU.load(env["kw"]["ledger"])) == 1                                   # claims never create ledger rows
    r = CI.handle_post(post(3, "Trade cancelled", reply=1), env["st"], **env["kw"]); assert r["outcome"] == "CANCEL_SUBMITTED"
    assert SU.load(env["kw"]["ledger"])[-1]["action"] == "CANCEL"


def test_not_submitted_cases(env, monkeypatch):
    assert CI.handle_post(post(5, "Good morning"), env["st"], **env["kw"])["outcome"] == "IGNORED_NOT_A_SIGNAL"
    assert CI.handle_post(post(6, "POSITIONAL SILVERM 147000 PE\nNEAR LEVEL 2550\nTARGET -3050\nSTOPLOSS - 2050"), env["st"], **env["kw"])["outcome"] in ("IGNORED_NOT_A_SIGNAL", "UNSUPPORTED_INSTRUMENT")        # not in the contest list
    monkeypatch.setattr(CI, "_market_open", lambda u: False)
    assert CI.handle_post(post(7, MCX), env["st"], **env["kw"])["outcome"] == "MARKET_CLOSED_NOT_SUBMITTED"
    monkeypatch.setattr(CI, "_market_open", lambda u: True)
    monkeypatch.setattr(CI.CT, "check", lambda *a, **k: {"problems": ["strike not captured"], "warnings": [], "last": None})
    assert CI.handle_post(post(8, MCX), env["st"], **env["kw"])["outcome"] == "UNSCORABLE_NOT_SUBMITTED"
    assert SU.load(env["kw"]["ledger"]) == []


def test_poll_only_whitelisted_channels_and_offset(env, monkeypatch):
    monkeypatch.setenv("ARENA_CHANNEL_IDS", "-100123")
    ups = {"result": [{"update_id": 10, "channel_post": post(1, HERO)}, {"update_id": 11, "channel_post": {**post(2, HERO), "chat": {"id": -999, "type": "channel"}}},
                      {"update_id": 12, "message": {"chat": {"id": 5, "type": "private"}, "text": "x"}}]}
    seen_calls = []
    def call(m, **q): seen_calls.append(q); return ups
    sp = str(env["tmp"] / "s.json")
    res = CI.poll("t", call=call, state_path=sp, **env["kw"])
    assert [r["outcome"] for r in res] == ["SUBMITTED"] and json.load(open(sp))["offset"] == 13
    CI.poll("t", call=lambda m, **q: seen_calls.append(q) or {"result": []}, state_path=sp, **env["kw"])
    assert seen_calls[-1]["offset"] == 13 and len(SU.load(env["kw"]["ledger"])) == 1


def test_expiry_hint_uses_only_the_matching_captured_expiry(env, monkeypatch):
    msg = "NIFTY 27 OCT 25350 PE\nABOVE 150\nTARGET 200/250\nSL 120"
    monkeypatch.setattr(CI.CT, "captured", lambda u, d, p=None: {"expiries": {"2026-10-22": {"strikes": []}, "2026-10-27": {"strikes": [25350.0]}}})
    r = CI.handle_post(post(21, msg), env["st"], **{**env["kw"], "now": FIXED_NOW}); assert r["outcome"] == "SUBMITTED" and r["expiry"] == "2026-10-27"
    r = CI.handle_post(post(22, msg.replace("27 OCT", "17 JULY")), env["st"], **{**env["kw"], "now": FIXED_NOW}); assert r["outcome"] == "NO_CAPTURED_EXPIRY_FOR_POST"


def test_entry_only_post_gets_estimator_stop_and_1r_2r_3r_ladder(env, monkeypatch):
    from app import sl_detector as D
    monkeypatch.setattr(CI.CT, "captured", lambda u, d, p=None: {"expiries": {"2026-10-27": {"strikes": [25350.0]}}})
    monkeypatch.setattr(D, "prior_ltps", lambda *a, **k: [150.0, 140.0, 130.0])
    monkeypatch.setattr(D, "record_prediction", lambda *a, **k: calls.append(a))
    calls = []
    r = CI.handle_post(post(31, "NIFTY 27 OCT 25350 PE\nABOVE :- 160"), env["st"], **{**env["kw"], "now": FIXED_NOW}); assert r["outcome"] == "SUBMITTED"
    rec = SU.load(env["kw"]["ledger"])[-1]
    assert rec["sl"] == 126.1 and rec["targets"] == [193.9, 227.8, 261.7] and "risk module" in rec["note"] and rec["entry"] == 160.0 and len(calls) == 1          # 0.97*130 ; R=33.9
    r = CI.handle_post(post(32, "NIFTY 27 OCT 25350 PE\nABOVE :- 160\nSL :- 140"), env["st"], **{**env["kw"], "now": FIXED_NOW})                                           # published stop kept, ladder from it
    rec = SU.load(env["kw"]["ledger"])[-1]; assert rec["sl"] == 140.0 and rec["targets"] == [180.0, 200.0, 220.0] and len(calls) == 1
    r = CI.handle_post(post(33, "NIFTY 27 OCT 25350 PE\nABOVE :- 160\nTARGET :- 200/250\nSL :- 140"), env["st"], **{**env["kw"], "now": FIXED_NOW})                  # fully published: untouched
    rec = SU.load(env["kw"]["ledger"])[-1]; assert rec["sl"] == 140.0 and rec["targets"] == [200.0, 250.0] and "risk module" not in rec["note"]


def test_channel_signals_carry_the_profile_default_quantity(env, monkeypatch, tmp_path):
    import json
    from app.arena import api as A
    (tmp_path / "pf.json").write_text(json.dumps({"INSTITUTION_TRADER": {"default_lots": 2}})); monkeypatch.setattr(A, "PARTICIPANTS_FILE", str(tmp_path / "pf.json"))
    assert CI.handle_post(post(41, HERO), env["st"], **env["kw"])["outcome"] == "SUBMITTED" and SU.load(env["kw"]["ledger"])[-1]["lots"] == 2                       # Mohit's profile: 2 lots
    kw = {**env["kw"], "participant": "SOMEONE_ELSE"}; assert CI.handle_post(post(42, HERO), env["st"], **kw)["outcome"] == "SUBMITTED" and SU.load(env["kw"]["ledger"])[-1]["lots"] == 1   # everybody else: 1 lot


def test_captured_fresh_bypasses_a_stale_cache_after_an_expiry_roll(monkeypatch):
    """2099-10-23: the first Mohit post after the SENSEX expiry roll read yesterday's cached expiry list and was dropped as NO_CAPTURED_EXPIRY."""
    import time
    from app.arena import contracts as CT_
    stale = {"day": "2099-10-22", "expiries": {"2099-10-22": {"strikes": [73600.0], "raw": "22OCT2026"}}}
    new = {"day": "2099-10-23", "expiries": {"2099-10-29": {"strikes": [73600.0], "raw": "29OCT2026"}}}
    key = ("ZZTEST", None, "/nonexistent-test-path"); calls = []

    def fake_compute(u, d=None, p=None):
        calls.append(1); CT_._cache[key] = (time.time(), new); return new
    monkeypatch.setattr(CT_, "_compute_captured", fake_compute); monkeypatch.setattr(CT_, "_persist", lambda: None); monkeypatch.setattr(CT_, "_refreshing", set())
    try:
        CT_._cache[key] = (time.time() - 3600, stale)
        assert CT_.captured("ZZTEST", None, "/nonexistent-test-path") == stale              # stale-while-revalidate returns the old list at once
        CT_._cache[key] = (time.time() - 3600, stale); calls.clear()
        assert CT_._real_captured_fresh("ZZTEST", None, "/nonexistent-test-path") == new and calls == [1]       # the fresh variant recomputes synchronously
        assert CT_._real_captured_fresh("ZZTEST", None, "/nonexistent-test-path") == new and calls == [1]       # ...and a young answer is reused, not recomputed
    finally:
        CT_._cache.pop(key, None)


def test_pick_expiry_and_hint_use_the_fresh_list(monkeypatch):
    seen = []
    monkeypatch.setattr(CI.CT, "captured_fresh", lambda u, d=None, p=None, max_age=30.0: seen.append(u) or {"expiries": {"2026-10-22": {}, "2026-10-29": {}}})
    assert CI.pick_expiry("SENSEX", "2026-10-23") == "2026-10-29" and seen == ["SENSEX"]
