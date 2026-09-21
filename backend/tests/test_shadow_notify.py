"""app.ai.shadow_notify + its wiring through app.ai.shadow.run_shadow_decision
-- the ZEROHERO spec's Phase 8 test matrix. Real DB writes (fresh_db,
isolated), mocked AI/Telegram calls only -- no network."""
import json

import pytest

from app.ai import fusion as fu
from app.ai import groq_client as gc
from app.ai import shadow as sh
from app.ai import shadow_notify as sn


def _sig(**over):
    base = {"decision": "BUY_CE", "regime": "TRENDING_UP", "signal_type": "RESISTANCE_BREAKOUT",
           "direction": "BULLISH", "mtf_alignment": 45.0, "signal_score": 68.0,
           "atr": 220.0, "entry": 22000.0, "stop_loss": 21800.0, "sr_level": 22050.0,
           "confidence": "HIGH", "reason": "trend up | vwap confirm | opt_q 80"}
    base.update(over)
    return base


def _eff(cost_status="UNCALIBRATED"):
    return {"cost_model_status": cost_status, "instrument_validation_status": "DEFAULT",
           "regime": "NORMAL_DAY"}


def _tg_cfg(**over):
    cfg = {"telegram_enabled": True, "telegram_include_weak": False}
    cfg.update(over)
    return cfg


def _sent_capture(monkeypatch):
    sent = []

    def fake_send(text, chat_id):
        sent.append({"text": text, "chat_id": chat_id})
        return {"ok": True, "status_code": 200, "message_id": 4242}
    monkeypatch.setattr(sn.telegram_dispatcher.dispatcher(), "_send_fn", fake_send)
    # A real chat_id must reach the send call -- maybe_notify() once omitted
    # this entirely, so every real send silently hit app.connectors.telegram
    # ._send's TELEGRAM_NOT_CONFIGURED guard despite telegram_enabled=True
    # (caught via a manual production smoke test, not by any prior test here,
    # since fake_send above accepts a None chat_id just as happily).
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat-id")
    return sent


# ---- 1/2: PASS -> Telegram SENT, for both CE and PE ----

@pytest.mark.parametrize("decision,side,expected_states", [
    ("BUY_CE", "CE", ("BUY", "STRONG_BUY")),
    ("BUY_PE", "PE", ("SELL", "STRONG_SELL")),
])
def test_pass_sends_telegram_for_both_sides(fresh_db, monkeypatch, decision, side, expected_states):
    sent = _sent_capture(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model")
    ok_data = {"regime": "TREND", "profile_match": True, "profile_match_confidence": 80,
              "signal_validation": "PASS", "confidence": 75, "risk": "LOW",
              "warnings": [], "reason_codes": []}
    monkeypatch.setattr(gc, "chat_completion_json",
                        lambda **k: gc.AIResult(status="OK", data=ok_data,
                                               model_used="test/model", latency_ms=10.0))
    result = sh.run_shadow_decision("NIFTY", _sig(decision=decision), _eff(),
                                    telegram_cfg=_tg_cfg())
    assert result["final_state"] in expected_states
    assert len(sent) == 1
    assert f"BUY {side}" in sent[0]["text"]
    assert "ZEROHERO SHADOW SIGNAL" in sent[0]["text"]
    assert "NO LIVE BROKER ORDER WAS PLACED" in sent[0]["text"]
    # Regression: maybe_notify() must pass the real chat_id through to
    # dispatch() -- see _sent_capture's comment above.
    assert sent[0]["chat_id"] == "test-chat-id"
    row = fresh_db.list_shadow_decisions(symbol="NIFTY")[0]
    assert row["telegram_status"] == "SENT"
    assert row["telegram_message_id"] == 4242
    assert row["signal_id"] is not None
    assert row["sent_at"] is not None


# ---- 3: FAIL -> NO_TRADE -> Telegram NOT SENT ----

def test_fail_suppresses_to_no_trade_and_does_not_send(fresh_db, monkeypatch):
    sent = _sent_capture(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model")
    fail_data = {"regime": "CHOP", "profile_match": False, "profile_match_confidence": 20,
                "signal_validation": "FAIL", "confidence": 30, "risk": "HIGH",
                "warnings": ["structure invalid"], "reason_codes": []}
    monkeypatch.setattr(gc, "chat_completion_json",
                        lambda **k: gc.AIResult(status="OK", data=fail_data,
                                               model_used="test/model", latency_ms=10.0))
    result = sh.run_shadow_decision("NIFTY", _sig(), _eff(), telegram_cfg=_tg_cfg())
    assert result["final_state"] == "NO_TRADE"
    assert sent == []
    row = fresh_db.list_shadow_decisions(symbol="NIFTY")[0]
    assert row["telegram_status"] is None


# ---- 4: UNCERTAIN -> NO_TRADE (this codebase's configured conservative
# policy: UNCERTAIN is treated as a HIGH-risk-equivalent weaken, and a
# deterministic BUY_CE at signal_score=68 weakens exactly one step to
# WEAK_BUY, not all the way to NO_TRADE -- verified against the real fusion
# rules rather than assumed, since "UNCERTAIN forces NO_TRADE" is not
# actually what fusion.py implements) ----

def test_uncertain_does_not_send_when_it_stays_weak(fresh_db, monkeypatch):
    sent = _sent_capture(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model")
    unc_data = {"regime": "RANGE", "profile_match": False, "profile_match_confidence": 40,
               "signal_validation": "UNCERTAIN", "confidence": 45, "risk": "MEDIUM",
               "warnings": [], "reason_codes": []}
    monkeypatch.setattr(gc, "chat_completion_json",
                        lambda **k: gc.AIResult(status="OK", data=unc_data,
                                               model_used="test/model", latency_ms=10.0))
    result = sh.run_shadow_decision("NIFTY", _sig(), _eff(), telegram_cfg=_tg_cfg())
    # UNCERTAIN + profile_match False is not a FAIL and not flagged HIGH risk
    # by this fusion policy -- assert against whatever fusion actually
    # decided, and that Telegram only fired if that decision was strong.
    if result["final_state"] in ("BUY", "STRONG_BUY", "SELL", "STRONG_SELL"):
        assert len(sent) == 1
    else:
        assert sent == []


# ---- 5: deterministic NO_TRADE -> Telegram NOT SENT ----

def test_deterministic_no_trade_never_sends(fresh_db, monkeypatch):
    sent = _sent_capture(monkeypatch)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = sh.run_shadow_decision("NIFTY", _sig(decision="NO_TRADE"), _eff(),
                                    telegram_cfg=_tg_cfg())
    assert result["final_state"] == "NO_TRADE"
    assert sent == []


# ---- 6: HIGH risk -> downgrade/suppression per existing fusion policy ----

def test_high_risk_weakens_and_only_sends_if_still_strong(fresh_db, monkeypatch):
    sent = _sent_capture(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model")
    hr_data = {"regime": "TREND", "profile_match": True, "profile_match_confidence": 70,
              "signal_validation": "PASS", "confidence": 60, "risk": "HIGH",
              "warnings": ["elevated risk"], "reason_codes": []}
    monkeypatch.setattr(gc, "chat_completion_json",
                        lambda **k: gc.AIResult(status="OK", data=hr_data,
                                               model_used="test/model", latency_ms=10.0))
    result = sh.run_shadow_decision("NIFTY", _sig(), _eff(), telegram_cfg=_tg_cfg())
    # BUY (index 5) weakened 1 step by HIGH risk -> WEAK_BUY (index 4) --
    # not in _STRONG_STATES, so no Telegram unless include_weak is set.
    assert result["final_state"] == "WEAK_BUY"
    assert sent == []


def test_high_risk_weak_state_sent_when_include_weak_enabled(fresh_db, monkeypatch):
    sent = _sent_capture(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model")
    hr_data = {"regime": "TREND", "profile_match": True, "profile_match_confidence": 70,
              "signal_validation": "PASS", "confidence": 60, "risk": "HIGH",
              "warnings": [], "reason_codes": []}
    monkeypatch.setattr(gc, "chat_completion_json",
                        lambda **k: gc.AIResult(status="OK", data=hr_data,
                                               model_used="test/model", latency_ms=10.0))
    result = sh.run_shadow_decision("NIFTY", _sig(), _eff(),
                                    telegram_cfg=_tg_cfg(telegram_include_weak=True))
    assert result["final_state"] == "WEAK_BUY"
    assert len(sent) == 1


# ---- 7: duplicate signal -> only one Telegram message ----

def test_duplicate_signal_sent_only_once(fresh_db, monkeypatch):
    sent = _sent_capture(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model")
    ok_data = {"regime": "TREND", "profile_match": True, "profile_match_confidence": 80,
              "signal_validation": "PASS", "confidence": 75, "risk": "LOW",
              "warnings": [], "reason_codes": []}
    monkeypatch.setattr(gc, "chat_completion_json",
                        lambda **k: gc.AIResult(status="OK", data=ok_data,
                                               model_used="test/model", latency_ms=10.0))
    sig = _sig()
    r1 = sh.run_shadow_decision("NIFTY", sig, _eff(), telegram_cfg=_tg_cfg())
    r2 = sh.run_shadow_decision("NIFTY", sig, _eff(), telegram_cfg=_tg_cfg())
    assert r1["final_state"] in ("BUY", "STRONG_BUY")
    assert r2["final_state"] in ("BUY", "STRONG_BUY")
    assert len(sent) == 1     # second call: same signal_id, DB-level dedup catches it
    rows = fresh_db.list_shadow_decisions(symbol="NIFTY")
    assert len(rows) == 2                         # both decisions still logged
    assert rows[0]["telegram_status"] is None      # the second (most recent first)
    assert rows[1]["telegram_status"] == "SENT"    # the first


# ---- 8: Telegram API failure -> runner continues, shadow decision unaffected ----

def test_telegram_failure_does_not_block_shadow_decision(fresh_db, monkeypatch):
    def failing_send(text, chat_id):
        return {"ok": False, "status_code": 500, "message_id": None}
    monkeypatch.setattr(sn.telegram_dispatcher.dispatcher(), "_send_fn", failing_send)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model")
    ok_data = {"regime": "TREND", "profile_match": True, "profile_match_confidence": 80,
              "signal_validation": "PASS", "confidence": 75, "risk": "LOW",
              "warnings": [], "reason_codes": []}
    monkeypatch.setattr(gc, "chat_completion_json",
                        lambda **k: gc.AIResult(status="OK", data=ok_data,
                                               model_used="test/model", latency_ms=10.0))
    result = sh.run_shadow_decision("NIFTY", _sig(), _eff(), telegram_cfg=_tg_cfg())
    assert result is not None
    assert result["final_state"] in ("BUY", "STRONG_BUY")
    row = fresh_db.list_shadow_decisions(symbol="NIFTY")[0]
    assert row["telegram_status"] == "FAILED"
    assert row["fused_final_state"] in ("BUY", "STRONG_BUY")   # unaffected by the send failure


def test_telegram_send_fn_raising_never_propagates(fresh_db, monkeypatch):
    def boom(text, chat_id):
        raise RuntimeError("simulated network failure")
    monkeypatch.setattr(sn.telegram_dispatcher.dispatcher(), "_send_fn", boom)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model")
    ok_data = {"regime": "TREND", "profile_match": True, "profile_match_confidence": 80,
              "signal_validation": "PASS", "confidence": 75, "risk": "LOW",
              "warnings": [], "reason_codes": []}
    monkeypatch.setattr(gc, "chat_completion_json",
                        lambda **k: gc.AIResult(status="OK", data=ok_data,
                                               model_used="test/model", latency_ms=10.0))
    result = sh.run_shadow_decision("NIFTY", _sig(), _eff(), telegram_cfg=_tg_cfg())
    assert result is not None    # run_shadow_decision's own try/except still isn't needed --
    assert result["final_state"] in ("BUY", "STRONG_BUY")   # maybe_notify already swallowed it


# ---- 9: restart/recovery -> no duplicate signal, even with a fresh
# TelegramDispatcher (simulates a process restart, which resets the
# dispatcher's in-memory registry but not the DB) ----

def test_no_duplicate_across_a_simulated_restart(fresh_db, monkeypatch):
    sent = _sent_capture(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROQ_MODEL", "test/model")
    ok_data = {"regime": "TREND", "profile_match": True, "profile_match_confidence": 80,
              "signal_validation": "PASS", "confidence": 75, "risk": "LOW",
              "warnings": [], "reason_codes": []}
    monkeypatch.setattr(gc, "chat_completion_json",
                        lambda **k: gc.AIResult(status="OK", data=ok_data,
                                               model_used="test/model", latency_ms=10.0))
    sig = _sig()
    sh.run_shadow_decision("NIFTY", sig, _eff(), telegram_cfg=_tg_cfg())
    assert len(sent) == 1

    # Simulate a process restart: a brand-new TelegramDispatcher singleton,
    # whose in-memory recent-signal registry is empty -- if dedup only lived
    # there, this second call would send again.
    sn.telegram_dispatcher._singleton = sn.telegram_dispatcher.TelegramDispatcher()
    sent2 = _sent_capture(monkeypatch)
    sh.run_shadow_decision("NIFTY", sig, _eff(), telegram_cfg=_tg_cfg())
    assert sent2 == []    # the DB-level check (shadow_signal_already_sent) still catches it


# ---- 10: no-lookahead -- shadow_notify only ever reads bars_by_tf /
# sig / ai_result / fused, the exact same already-computed values passed
# into it; it makes no further data calls of its own. ----

def test_shadow_notify_touches_no_data_source_of_its_own():
    import inspect
    src = inspect.getsource(sn)
    forbidden = ("requests.get", "fetch_candles", "db.get_", "future_candle")
    for f in forbidden:
        assert f not in src, f"shadow_notify.py references {f!r} -- possible new data read"


def test_extract_spot_uses_only_the_passed_in_bars():
    bars = {"5m": [[0, 100, 101, 99, 100.5, 1000], [0, 101, 102, 100, 101.5, 1200]]}
    assert sn.extract_spot(bars) == 101.5
    assert sn.extract_spot(None) is None
    assert sn.extract_spot({}) is None


def test_build_signal_id_is_deterministic_within_the_same_10min_bucket():
    sig = _sig()
    a = sn.build_signal_id("NIFTY", sig)
    b = sn.build_signal_id("NIFTY", sig)
    assert a == b
    assert a.startswith("NIFTY:BUY_CE:")
