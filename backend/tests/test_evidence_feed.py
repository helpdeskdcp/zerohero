import json
import os
import time

import pytest

from app.connectors import telegram as TG

_ORIG_SEND = TG._send          # captured at import: conftest stubs telegram._send per test so that no test touches the network
from app.evidence_feed import publish as EV
from app.forward_test import ledger as L

SIG = {"decision": "BUY_CE", "strike": 23000.0, "expiry": "27OCT2026", "entry": 100.0, "stop_loss": 85.0, "target_1": 120.0, "target_2": 140.0, "regime": "TRENDING_DOWN", "signal_type": "SUPPORT_BREAKDOWN", "confidence": "MEDIUM"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(EV, "CHAT_FILE", str(tmp_path / "no_chat_id.txt"))                                   # never read the real hot-config file
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "111"); monkeypatch.setenv("TELEGRAM_SIGNALS_CHANNEL_ID", "-100222")
    monkeypatch.delenv("EVIDENCE_CHAT_ID", raising=False); monkeypatch.delenv("TELEGRAM_EVIDENCE_ONLY", raising=False)
    posted = []

    class R:
        ok = True; status_code = 200
        def json(self): return {"result": {"message_id": 77}}
    monkeypatch.setattr(TG, "_send", _ORIG_SEND)
    monkeypatch.setattr(TG.requests, "post", lambda url, json=None, timeout=0: posted.append(json) or R())
    EV._seen.clear(); TG._suppressed.update(n=0, last_ts=None); return posted


# ---------------- telegram gate ----------------
def test_gate_is_off_under_pytest_by_default_and_on_when_forced(env, monkeypatch):
    assert TG.evidence_only() is False and TG._send("x", "-100222")["ok"] is True
    monkeypatch.setenv("TELEGRAM_EVIDENCE_ONLY", "force"); assert TG.evidence_only() is True


def test_evidence_only_blocks_signals_channel_but_not_private_chat_or_evidence(env, monkeypatch):
    monkeypatch.setenv("TELEGRAM_EVIDENCE_ONLY", "force"); posted = env
    r = TG._send("some other engine's signal", "-100222"); assert r == {"ok": False, "reason": "SUPPRESSED_EVIDENCE_ONLY"} and posted == [] and TG.suppressed_stats()["n"] == 1
    assert TG._send("ops/lifecycle alert", "111")["ok"] is True and posted[-1]["chat_id"] == "111"
    assert TG._send("evidence", "-100222", evidence=True)["ok"] is True and posted[-1]["chat_id"] == "-100222"
    monkeypatch.setenv("TELEGRAM_EVIDENCE_ONLY", "0"); assert TG._send("old behaviour", "-100222")["ok"] is True


def test_public_notifiers_are_silenced_in_evidence_only_mode(env, monkeypatch):
    monkeypatch.setenv("TELEGRAM_EVIDENCE_ONLY", "force"); posted = env
    TG.notify_signal({"signal_id": "S1", "final_decision": "APPROVED", "underlying": "NIFTY", "direction": "BUY"}); assert [p for p in posted if p["chat_id"] == "-100222"] == []


# ---------------- evidence feed ----------------
def test_post_chain_dedupe_destination_and_content(env, tmp_path, monkeypatch):
    sent = []
    sender = lambda text, cid: sent.append((cid, text)) or {"ok": True, "message_id": 5, "status_code": 200}
    p = str(tmp_path / "posts.jsonl"); ts = 1_792_200_000.0
    txt = EV.format_signal(label="engine AUTOSCALP", rid="T1", ts=ts, underlying="NIFTY", strike=23000.0, option_type="CE", expiry="27OCT2026", entry=100.0, sl=85.0, t1=120.0, t2=140.0)
    a = EV._post("AUTOSCALP", "T1", txt, ts, sender=sender, path=p); assert a["telegram"]["message_id"] == 5 and sent[0][0] == "-100222"
    assert EV._post("AUTOSCALP", "T1", txt, ts, sender=sender, path=p) is None and len(sent) == 1                      # no duplicate post
    monkeypatch.setenv("EVIDENCE_CHAT_ID", "-100999"); EV._post("AUTOSCALP", "T2", txt, ts, sender=sender, path=p); assert sent[-1][0] == "-100999"      # dedicated evidence group wins
    assert L.verify_chain(p)[0] and "PAPER-TRADING evidence record" in txt and "NIFTY 23000 CE" in txt and "IST" in txt and "not a recommendation" in txt


def test_post_records_failure_and_never_raises(env, tmp_path):
    p = str(tmp_path / "posts.jsonl")
    fail = lambda text, cid: {"ok": False, "reason": "boom"}
    r = EV._post("AUTOSCALP", "T9", "x", 1.0, sender=fail, path=p); assert r["telegram"]["ok"] is False and r["telegram"]["reason"] == "boom"
    EV._post_ok = EV._safe("AUTOSCALP", "T10", "x", 1.0, sender=lambda t, c: 1 / 0, path=p)                                    # sender explodes -> swallowed
    assert EV.publish_open_trade("NIFTY", {}, {}, "x") is None and EV.publish_arm_signal({}) is None                       # garbage in, no exception


def test_enabled_flag_and_force(monkeypatch):
    assert EV.enabled() is False                                                                                      # pytest default
    monkeypatch.setenv("EVIDENCE_FEED", "force"); assert EV.enabled() is True
    monkeypatch.setenv("EVIDENCE_FEED", "0"); assert EV.enabled() is False


def test_open_trade_and_arm_signal_publish_hooks(env, monkeypatch):
    seen = []
    monkeypatch.setenv("EVIDENCE_FEED", "force"); monkeypatch.setattr(EV, "post_async", lambda kind, rid, text, ts, **kw: seen.append((kind, rid, text)))
    EV.publish_open_trade("NIFTY", SIG, {"trade_id": "TR-1"}, "ASC-1")
    assert seen[0][0] == "AUTOSCALP" and seen[0][1] == "TR-1" and "NIFTY 23000 CE" in seen[0][2] and "frozen" in seen[0][2]
    EV.publish_arm_signal({"id": 5, "ts": 1.0, "symbol": "BANKNIFTY", "decision": "BUY_PE", "strike": 52000.0, "expiry": "27OCT2026", "entry": 200.0, "stop_loss": 170.0, "target_1": 240.0, "frozen_blocked": 1})
    assert seen[1][0] == "ARM" and "ACTIVE-ONLY" in seen[1][2] and "EXPLORATORY ARM" in seen[1][2]
    EV.publish_arm_signal({"id": 6, "ts": 1.0, "symbol": "NIFTY", "decision": "BUY_CE", "strike": 23000.0, "expiry": "x", "entry": 100.0, "stop_loss": 85.0, "target_1": 120.0, "frozen_blocked": 0}); assert "COMMON" in seen[2][2]


def test_arm_record_triggers_evidence_and_runner_hook_is_guarded(tmp_path, monkeypatch):
    from app.active_arm import shadow as SH
    seen = []; monkeypatch.setattr(EV, "publish_arm_signal", lambda rec: seen.append(rec)); SH._last.clear()
    assert SH.record("NIFTY", {**SIG, "max_hold_sec": 1500.0}, {"decision": "NO_TRADE"}, "NFO", path=str(tmp_path / "a.db"), now=1000.0) is True
    assert seen and seen[0]["frozen_blocked"] == 1 and seen[0]["symbol"] == "NIFTY" and seen[0]["id"] >= 1
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); src = open(os.path.join(root, "app", "autoscalp", "runner.py")).read()
    i = src.index("_evidence.publish_open_trade"); assert "try:" in src[i - 250:i] and "except Exception:" in src[i:i + 200] and src.index("row = open_trade(") < i


def test_no_sebi_wording_in_arena_facing_material():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    files = ["backend/ARENA_TERMS_2026-10-20.md", "backend/INSTITUTION_ARENA_GUIDE.md", "backend/HEAD_TO_HEAD_ARENA_PROPOSAL_2026-10-20.md", "frontend/static/arena.html", "frontend/static/js/arena.js", "frontend/index.html",
             "backend/app/arena/api.py", "backend/app/arena/settle.py", "backend/app/arena/submit.py", "backend/app/arena/run.py", "backend/data/arena/INSTITUTION_ACCESS.txt", "backend/scripts/arena_submit.py", "backend/scripts/arena_run.py"]
    bad = [f for f in files if os.path.exists(os.path.join(root, f)) and "sebi" in open(os.path.join(root, f), encoding="utf-8").read().lower()]
    assert bad == [], f"arena-facing files must not use the SEBI name: {bad}"
    assert not os.path.exists(os.path.join(root, "backend", "SEBI_ARENA_GUIDE.md"))


def test_chat_id_priority_with_hot_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111"); monkeypatch.setenv("TELEGRAM_SIGNALS_CHANNEL_ID", "-100222"); monkeypatch.delenv("EVIDENCE_CHAT_ID", raising=False)
    monkeypatch.setattr(EV, "CHAT_FILE", str(tmp_path / "chat_id.txt"))
    assert EV.chat_id() == "-100222"                                                  # no evidence group yet: signals channel
    (tmp_path / "chat_id.txt").write_text("-100777\n"); assert EV.chat_id() == "-100777"                       # hot file, no restart
    monkeypatch.setenv("EVIDENCE_CHAT_ID", "-100888"); assert EV.chat_id() == "-100888"                       # env wins
    monkeypatch.delenv("TELEGRAM_SIGNALS_CHANNEL_ID"); monkeypatch.delenv("EVIDENCE_CHAT_ID"); (tmp_path / "chat_id.txt").unlink(); assert EV.chat_id() == "111"
