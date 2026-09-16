"""
Runner-level wiring for app.signal_gate.final_signal_gate: opt-in, default
OFF -> Telegram entry publishing unchanged; when enabled, only an APPROVED
FinalSignalDecision may reach Telegram, while the paper trade + audit rows
are unaffected either way (research/calibration sample generation must not
change). Mirrors tests/test_scalp_strategy_sr_gate.py's mocking style.
"""
import asyncio

from tests.test_autoscalp import _runner, _CHAIN

from app.signal_gate import final_signal_gate as fsg_mod


def setup_function(_):
    fsg_mod._LAST_APPROVED.clear()
    fsg_mod._ACTIVE_SIGNAL.clear()


def _sig(**kw):
    base = dict(decision="BUY_PE", signal_type="SUPPORT_BREAKDOWN", direction="BEARISH",
               strike=24100, token="PE24100", tradingsymbol="NIFTY24100PE",
               expiry="2026-09-03", entry=95.0, stop_loss=83.0, target_1=116.0,
               target_2=128.0, trailing_stop=8.0, max_hold_sec=1500,
               probability=0.58, confidence="MEDIUM", ev=6.0, rr=2.0,
               regime="TRENDING_DOWN", mtf_alignment=-30.0, signal_score=63.0,
               component_scores={"htf": 0.9, "vwap": 0.9, "momentum": 0.9, "volume": 0.9, "oi": 0.9},
               reason="test", support=24080, resistance=24150, support_strength=60,
               resistance_strength=62, sr_level=24085, sr_side="SUPPORT", atr=11.0, vwap=24110.0,
               sr_confirmation={"verdict": "CONFIRM", "sr_score": 95.0, "reason": "stub"},
               chain_bias=None)
    base.update(kw)
    return base


def test_gate_disabled_sends_telegram_as_before(fresh_db, monkeypatch):
    r, feed = _runner(monkeypatch, _sig())
    sent = []
    monkeypatch.setattr(r, "_tg_send", lambda key, *a, **k: sent.append(key))
    r.arm()
    asyncio.run(r.tick_once())
    assert any(k.startswith("entry:") for k in sent)


def test_gate_enabled_approved_sends_telegram_and_tracks_active_signal(fresh_db, monkeypatch):
    r, feed = _runner(monkeypatch, _sig())
    r.set_config({"final_signal_gate": {"enabled": True}})
    sent = []
    monkeypatch.setattr(r, "_tg_send", lambda key, *a, **k: sent.append(key))
    r.arm()
    asyncio.run(r.tick_once())
    assert any(k.startswith("entry:") for k in sent)
    assert r.last_final_signal_gate["state"] == "APPROVED"
    assert "NIFTY" in r._active_fsg_fingerprint


def test_gate_enabled_rejected_blocks_telegram_but_trade_still_opens(monkeypatch, fresh_db):
    # rr below the gate's minimum -> REJECT, but the underlying strategy still
    # opened a paper trade (research/calibration must not be affected)
    r, feed = _runner(monkeypatch, _sig(rr=0.5))
    r.set_config({"final_signal_gate": {"enabled": True}})
    sent = []
    monkeypatch.setattr(r, "_tg_send", lambda key, *a, **k: sent.append(key))
    r.arm()
    asyncio.run(r.tick_once())
    assert not any(k.startswith("entry:") for k in sent)
    assert r.last_final_signal_gate["state"] == "REJECT"
    assert fresh_db.list_trades(strategy="AUTOSCALP") != []


def test_gate_enabled_sr_contradict_blocks_telegram(fresh_db, monkeypatch):
    r, feed = _runner(monkeypatch, _sig(sr_confirmation={"verdict": "CONTRADICT", "reason": "stub"}))
    r.set_config({"final_signal_gate": {"enabled": True}})
    sent = []
    monkeypatch.setattr(r, "_tg_send", lambda key, *a, **k: sent.append(key))
    r.arm()
    asyncio.run(r.tick_once())
    assert not any(k.startswith("entry:") for k in sent)
    assert r.last_final_signal_gate["state"] == "REJECT"
