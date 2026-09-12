"""
app/telegram_dispatcher.py -- canonical Telegram Signal Dispatcher.
Pure in-memory, send_fn injected (never hits the real Telegram API).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.telegram_dispatcher import TelegramDispatcher, dispatch  # noqa: E402


def _fake_sender():
    sent = []

    def send_fn(text, chat_id):
        sent.append({"text": text, "chat_id": chat_id})
        return {"ok": True}
    return sent, send_fn


def test_first_signal_for_an_underlying_sends_unbannered():
    sent, send_fn = _fake_sender()
    d = TelegramDispatcher(send_fn=send_fn)
    rec = d.dispatch(source_engine="autoscalp", underlying="NIFTY", direction="BUY_CE",
                      text="entry card body")
    assert rec.status == "SENT"
    assert rec.agreement == "NONE"
    assert len(sent) == 1
    assert sent[0]["text"] == "entry card body"   # no banner manufactured from nothing


def test_same_direction_from_a_different_engine_is_confirmed():
    sent, send_fn = _fake_sender()
    d = TelegramDispatcher(send_fn=send_fn)
    d.dispatch(source_engine="autoscalp", underlying="NIFTY", direction="BUY_CE", text="A")
    rec = d.dispatch(source_engine="orderflow", underlying="NIFTY", direction="BULLISH", text="B")
    assert rec.agreement == "CONFIRMED"
    assert "CONFIRMED" in sent[1]["text"]
    assert "autoscalp" in sent[1]["text"]
    assert sent[1]["text"].endswith("B")


def test_opposing_direction_from_a_different_engine_is_conflict():
    sent, send_fn = _fake_sender()
    d = TelegramDispatcher(send_fn=send_fn)
    d.dispatch(source_engine="autoscalp", underlying="NIFTY", direction="BUY_CE", text="A")
    rec = d.dispatch(source_engine="reversal_scan", underlying="NIFTY", direction="BUY_PE", text="B")
    assert rec.agreement == "CONFLICT"
    assert "CONFLICT" in sent[1]["text"]
    assert rec.status == "SENT"   # section 16: never suppress the signal itself


def test_same_engine_repeating_does_not_trigger_agreement_logic():
    sent, send_fn = _fake_sender()
    d = TelegramDispatcher(send_fn=send_fn, exact_repeat_window_sec=0)
    d.dispatch(source_engine="autoscalp", underlying="NIFTY", direction="BUY_CE", text="A")
    rec = d.dispatch(source_engine="autoscalp", underlying="NIFTY", direction="BUY_PE", text="B")
    assert rec.agreement == "NONE"   # same engine flip-flopping isn't cross-engine conflict


def test_exact_repeat_from_the_same_engine_is_suppressed():
    sent, send_fn = _fake_sender()
    d = TelegramDispatcher(send_fn=send_fn)
    d.dispatch(source_engine="reversal_scan", underlying="NIFTY", direction="BUY_CE", text="A")
    rec = d.dispatch(source_engine="reversal_scan", underlying="NIFTY", direction="BUY_CE", text="A2")
    assert rec.status == "SUPPRESSED_DUPLICATE"
    assert len(sent) == 1   # the second call never reached send_fn


def test_repeat_outside_the_window_is_not_suppressed():
    sent, send_fn = _fake_sender()
    d = TelegramDispatcher(send_fn=send_fn, exact_repeat_window_sec=0.01)
    d.dispatch(source_engine="reversal_scan", underlying="NIFTY", direction="BUY_CE", text="A")
    import time
    time.sleep(0.02)
    rec = d.dispatch(source_engine="reversal_scan", underlying="NIFTY", direction="BUY_CE", text="A2")
    assert rec.status == "SENT"
    assert len(sent) == 2


def test_agreement_expires_outside_the_agreement_window():
    sent, send_fn = _fake_sender()
    d = TelegramDispatcher(send_fn=send_fn, agreement_window_sec=0.01, exact_repeat_window_sec=0.01)
    d.dispatch(source_engine="autoscalp", underlying="NIFTY", direction="BUY_CE", text="A")
    import time
    time.sleep(0.02)
    rec = d.dispatch(source_engine="orderflow", underlying="NIFTY", direction="BUY_CE", text="B")
    assert rec.agreement == "NONE"   # too old to count as corroboration


def test_different_underlyings_never_interact():
    sent, send_fn = _fake_sender()
    d = TelegramDispatcher(send_fn=send_fn)
    d.dispatch(source_engine="autoscalp", underlying="NIFTY", direction="BUY_CE", text="A")
    rec = d.dispatch(source_engine="orderflow", underlying="BANKNIFTY", direction="BUY_PE", text="B")
    assert rec.agreement == "NONE"


def test_structural_break_state_attached_when_tracked(monkeypatch):
    sent, send_fn = _fake_sender()
    d = TelegramDispatcher(send_fn=send_fn)

    import app.structural_break.evaluator as ev
    monkeypatch.setattr(ev, "tracked_scopes",
                         lambda: [{"scope_key": "NIFTY", "state": "WATCH", "n_evaluations": 3}])
    rec = d.dispatch(source_engine="autoscalp", underlying="NIFTY", direction="BUY_CE", text="A")
    assert rec.structural_break_state == "WATCH"
    assert rec.model_health == "WATCHING"
    assert "Structural Break: WATCH" in sent[0]["text"]
    assert "Model Health: WATCHING" in sent[0]["text"]


def test_no_structural_break_footer_when_untracked():
    sent, send_fn = _fake_sender()
    d = TelegramDispatcher(send_fn=send_fn)
    rec = d.dispatch(source_engine="autoscalp", underlying="SOME_UNTRACKED_SYMBOL",
                      direction="BUY_CE", text="A")
    assert rec.structural_break_state is None
    assert "Structural Break" not in sent[0]["text"]


def test_edge_score_and_microstructure_are_never_fabricated():
    sent, send_fn = _fake_sender()
    d = TelegramDispatcher(send_fn=send_fn)
    rec = d.dispatch(source_engine="autoscalp", underlying="NIFTY", direction="BUY_CE", text="A")
    assert rec.edge_score is None
    assert rec.microstructure is None


def test_direction_normalization_recognizes_engine_specific_vocab():
    sent, send_fn = _fake_sender()
    d = TelegramDispatcher(send_fn=send_fn)
    d.dispatch(source_engine="autoscalp", underlying="NIFTY", direction="BUY_CE", text="A")
    rec = d.dispatch(source_engine="orderflow", underlying="NIFTY", direction="buy", text="B")
    assert rec.agreement == "CONFIRMED"   # BUY_CE and "buy" both normalize to BULLISH


def test_module_level_dispatch_never_raises_even_on_internal_error(monkeypatch):
    import app.telegram_dispatcher as td

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(td, "dispatcher", _boom)
    out = dispatch(source_engine="x", underlying="NIFTY", direction="BUY_CE", text="A")
    assert out["status"] == "ERROR"
