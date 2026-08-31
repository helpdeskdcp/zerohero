"""P7 autonomous PAPER runner — aggregator, safeguards, runner wiring.
Fake feed / chain provider; no broker, no network. LIVE never reached."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.autoscalp.aggregator import CandleAggregator
from app.autoscalp.safeguards import Safeguards
from app.autoscalp import runner as ascr
from app.execution import killswitch


# ---------------- CandleAggregator ----------------
def test_aggregator_builds_and_excludes_partial_bar():
    a = CandleAggregator(tfs=("1m", "3m"))
    base = int(time.mktime(time.strptime("2026-08-27 09:15:00", "%Y-%m-%d %H:%M:%S")))
    base -= time.timezone  # feed epoch is UTC; buckets are IST -> just be consistent
    # 4 minutes of 1-min ticks, 3 ticks/min, rising price
    px = 100.0
    for m in range(4):
        for s in (5, 25, 50):
            a.add_tick(base + m * 60 + s, px)
            px += 0.5
    one = a.bars("1m", now_epoch=base + 3 * 60 + 55)
    # minute 3 bar still forming at :55 -> only minutes 0,1,2 are closed
    assert len(one) == 3
    assert one[0]["o"] == 100.0 and one[0]["c"] == 101.0
    assert one[0]["h"] >= one[0]["l"]
    # with closed_only off we see the forming bar too
    assert len(a.bars("1m", closed_only=False)) == 4


def test_aggregator_snapshot_multi_tf():
    a = CandleAggregator()
    base = 1_800_000_000
    for i in range(20 * 60):        # 20 min of 1/sec ticks
        a.add_tick(base + i, 100 + i * 0.001)
    snap = a.snapshot(now_epoch=base + 20 * 60 + 1)
    assert set(snap) == {"1m", "3m", "5m", "15m", "30m"}
    assert len(snap["1m"]) >= 19 and len(snap["5m"]) >= 3


# ---------------- Safeguards ----------------
@pytest.fixture(autouse=True)
def _ks_off(fresh_db, monkeypatch):
    killswitch.deactivate("test")
    # sandbox clock is a weekend; pin safeguard wall-clock to Wed noon IST
    from app.autoscalp import safeguards as _sg
    monkeypatch.setattr(_sg, "_mod_now", lambda: (720, 2))
    yield
    killswitch.deactivate("test")


def _base_check(sg, **over):
    kw = dict(open_count=0, feed_connected=True, feed_age_sec=1.0, underlying="NIFTY",
              side="PE", open_keys=set(), option_premium=90.0, spread_pct=0.2,
              realised_pnl_today=0.0)
    kw.update(over)
    return sg.check_entry(**kw)


def test_safeguards_killswitch_blocks(fresh_db, monkeypatch):
    monkeypatch.setattr(ascr, "_mod", lambda *_a, **_k: None, raising=False)
    sg = Safeguards({"session_start_hhmm": "00:00", "daily_cutoff_hhmm": "23:59"})
    killswitch.activate("test-halt")
    ok, why = _base_check(sg)
    assert ok is False and "kill switch" in why


def test_safeguards_stale_feed_and_disconnect_fail_closed(fresh_db):
    sg = Safeguards({"session_start_hhmm": "00:00", "daily_cutoff_hhmm": "23:59"})
    assert _base_check(sg, feed_age_sec=99)[0] is False
    assert _base_check(sg, feed_age_sec=None)[0] is False
    assert _base_check(sg, feed_connected=False)[0] is False


def test_safeguards_concurrency_duplicate_consec_and_dailyloss(fresh_db):
    sg = Safeguards({"session_start_hhmm": "00:00", "daily_cutoff_hhmm": "23:59",
                     "max_concurrent": 1, "max_consecutive_losses": 3, "max_daily_loss": 1000})
    assert _base_check(sg, open_count=1)[0] is False                      # concurrency
    assert _base_check(sg, open_keys={("NIFTY", "PE")})[0] is False       # duplicate
    sg.on_trade_closed(-50); sg.on_trade_closed(-50); sg.on_trade_closed(-50)
    assert _base_check(sg)[0] is False and sg.consecutive_losses == 3     # consec losses
    sg.consecutive_losses = 0
    ok, why = _base_check(sg, realised_pnl_today=-1500)
    assert ok is False and "daily loss" in why
    # sticky halt: even a recovered pnl stays halted for the session
    assert _base_check(sg, realised_pnl_today=0.0)[0] is False


def test_safeguards_premium_and_spread(fresh_db):
    sg = Safeguards({"session_start_hhmm": "00:00", "daily_cutoff_hhmm": "23:59",
                     "min_option_premium": 8, "max_spread_pct": 1.0})
    assert _base_check(sg, option_premium=3.0)[0] is False
    assert _base_check(sg, spread_pct=2.5)[0] is False
    assert _base_check(sg)[0] is True


# ---------------- Runner wiring ----------------
class FakeFeed:
    def __init__(self, marks):
        self.marks = marks                 # token -> ltp
        self.subscribed = []
        self.connected = True

    def subscribe(self, toks):
        self.subscribed = toks

    def get_ltp(self, token, *_a, **_k):
        return self.marks.get(str(token))

    def status(self):
        return {"connected": self.connected, "last_msg_age_sec": 1.0, "marks": {}}


_CHAIN = [
    {"strike": 24100, "ce": {"ltp": 130.0, "oi": 500000, "oi_chg": 0, "vol_delta": 20000,
                             "delta": 0.5, "gamma": 0.001, "theta": -18, "vega": 9, "iv": 13,
                             "token": "CE24100", "tradingsymbol": "NIFTY24100CE", "expiry": "2026-09-03"},
     "pe": {"ltp": 95.0, "oi": 900000, "oi_chg": 300000, "vol_delta": 40000,
            "delta": -0.5, "gamma": 0.001, "theta": -18, "vega": 9, "iv": 13,
            "token": "PE24100", "tradingsymbol": "NIFTY24100PE", "expiry": "2026-09-03"}},
]


def _runner(monkeypatch, decide_ret):
    feed = FakeFeed({"99926000": 24095.0, "CE24100": 130.0, "PE24100": 95.0})
    r = ascr.AutoScalpRunner(feed=feed, chain_provider=lambda *_a, **_k: _CHAIN,
                             telegram_fn=lambda *_a: None, now_fn=lambda: 1_800_000_000.0)
    r.set_config({"safeguards": {"allow_weekend": True, "session_start_hhmm": "00:00",
                                "daily_cutoff_hhmm": "23:59"}})
    # NIFTY seed resolves to token 99926000
    monkeypatch.setattr(ascr, "decide_from_context", lambda *a, **k: decide_ret)
    # give the NIFTY aggregator >= 20 5m bars so _evaluate proceeds
    from app.autoscalp.aggregator import CandleAggregator
    agg = CandleAggregator()
    for i in range(140 * 60):
        agg.add_tick(1_800_000_000.0 - (140 * 60) + i, 24100 - i * 0.0004)
    r._aggs["NIFTY"] = agg
    return r, feed


def test_runner_disarmed_takes_no_action(fresh_db, monkeypatch):
    r, _ = _runner(monkeypatch, {"decision": "BUY_PE"})
    r.disarm()
    asyncio.run(r.tick_once())
    assert fresh_db.list_scalp_signals(source="LIVE") == []
    assert r.status()["live_trading"] is False


def test_runner_opens_paper_trade_and_persists(fresh_db, monkeypatch):
    sig = {"decision": "BUY_PE", "signal_type": "SUPPORT_BREAKDOWN", "direction": "BEARISH",
           "strike": 24100, "token": "PE24100", "tradingsymbol": "NIFTY24100PE",
           "expiry": "2026-09-03", "entry": 95.0, "stop_loss": 83.0, "target_1": 116.0,
           "target_2": 128.0, "trailing_stop": 8.0, "max_hold_sec": 1500,
           "probability": 0.58, "confidence": "MEDIUM", "ev": 6.0, "rr": 1.6,
           "regime": "TRENDING_DOWN", "mtf_alignment": -30.0, "signal_score": 63.0,
           "component_scores": {"x": 1}, "reason": "test", "support": 24080,
           "resistance": 24150, "support_strength": 60, "resistance_strength": 62,
           "sr_level": 24085, "sr_side": "SUPPORT", "atr": 11.0, "vwap": 24110.0}
    r, feed = _runner(monkeypatch, sig)
    r.arm()
    asyncio.run(r.tick_once())

    sigs = fresh_db.list_scalp_signals(source="LIVE")
    assert len(sigs) == 1 and sigs[0]["decision"] == "BUY_PE" and sigs[0]["status"] == "OPEN"
    assert sigs[0]["opt_token"] == "PE24100" and sigs[0]["source"] == "LIVE"
    trades = fresh_db.list_trades(strategy="AUTOSCALP")
    assert len(trades) == 1 and trades[0]["status"] == "OPEN" and trades[0]["symboltoken"] == "PE24100"
    snaps = fresh_db.list_live_snapshots(symbol="NIFTY")
    assert len(snaps) == 1 and snaps[0]["decision"] == "BUY_PE" and snaps[0]["source"] == "LIVE"

    # PE premium jumps past target -> monitor closes it, outcome recorded
    feed.marks["PE24100"] = 118.0
    asyncio.run(r.tick_once())
    trades = fresh_db.list_trades(strategy="AUTOSCALP")
    assert trades[0]["status"] == "CLOSED" and trades[0]["exit_reason"] in ("TARGET", "TRAIL")
    sigs = fresh_db.list_scalp_signals(source="LIVE")
    assert sigs[0]["status"] == "CLOSED" and sigs[0]["resolved"] == 1 and sigs[0]["outcome"] == "WIN"


def test_runner_safeguard_blocks_entry(fresh_db, monkeypatch):
    r, _ = _runner(monkeypatch, {"decision": "BUY_PE", "signal_type": "SUPPORT_BREAKDOWN",
                                 "direction": "BEARISH", "strike": 24100, "entry": 3.0,
                                 "stop_loss": 2.0, "target_1": 5.0, "token": "PE24100",
                                 "regime": "TRENDING_DOWN"})
    r.arm()
    # premium 3.0 < min_option_premium 8 -> blocked
    asyncio.run(r.tick_once())
    assert fresh_db.list_trades(strategy="AUTOSCALP") == []


def test_runner_config_roundtrip_and_unknown_field(fresh_db):
    r = ascr.AutoScalpRunner()
    c = r.set_config({"symbols": ["NIFTY", "BANKNIFTY"], "decide_every_sec": 45})
    assert c["symbols"] == ["NIFTY", "BANKNIFTY"] and c["decide_every_sec"] == 45
    assert r.get_config()["symbols"] == ["NIFTY", "BANKNIFTY"]
    with pytest.raises(ValueError):
        r.set_config({"nonsense": 1})


def test_tg_send_confidence_gate_and_dedup(fresh_db):
    # clock is fixed by now_fn, so a repeat inside telegram_dedup_sec is dropped
    r = ascr.AutoScalpRunner(now_fn=lambda: 1_800_000_000.0)
    sent = []
    r._telegram = lambda text: sent.append(text)

    # default telegram_min_confidence == HIGH -> MEDIUM/LOW/None are gated out
    r._tg_send("k1", "med", conf="MEDIUM")
    r._tg_send("k1", "low", conf="LOW")
    r._tg_send("k1", "none", conf=None)
    assert sent == []

    # HIGH passes once, then the same key is de-duplicated
    r._tg_send("k1", "high", conf="HIGH")
    r._tg_send("k1", "high-again", conf="HIGH")
    assert sent == ["high"]

    # a different key still gets through
    r._tg_send("k2", "other", conf="HIGH")
    assert sent == ["high", "other"]

    # lowering the bar lets MEDIUM through
    r.set_config({"telegram_min_confidence": "MEDIUM"})
    r._tg_send("k3", "med-ok", conf="MEDIUM")
    assert sent[-1] == "med-ok"

    # dedup can be disabled explicitly
    r._tg_send("k4", "a", conf="HIGH", dedup=False)
    r._tg_send("k4", "b", conf="HIGH", dedup=False)
    assert sent[-2:] == ["a", "b"]


def test_runner_medium_confidence_opens_trade_but_no_telegram(fresh_db, monkeypatch):
    sig = {"decision": "BUY_PE", "signal_type": "SUPPORT_BREAKDOWN", "direction": "BEARISH",
           "strike": 24100, "token": "PE24100", "tradingsymbol": "NIFTY24100PE",
           "expiry": "2026-09-03", "entry": 95.0, "stop_loss": 83.0, "target_1": 116.0,
           "target_2": 128.0, "trailing_stop": 8.0, "max_hold_sec": 1500,
           "probability": 0.58, "confidence": "MEDIUM", "ev": 6.0, "rr": 1.6,
           "regime": "TRENDING_DOWN", "mtf_alignment": -30.0, "signal_score": 63.0,
           "component_scores": {}, "reason": "test", "support": 24080, "resistance": 24150,
           "support_strength": 60, "resistance_strength": 62, "sr_level": 24085,
           "sr_side": "SUPPORT", "atr": 11.0, "vwap": 24110.0}
    r, _ = _runner(monkeypatch, sig)
    sent = []
    r._telegram = lambda text: sent.append(text)
    r.arm()
    asyncio.run(r.tick_once())
    # trade still opens in PAPER + persists, but no MEDIUM card on Telegram
    assert len(fresh_db.list_trades(strategy="AUTOSCALP")) == 1
    assert sent == []


# ---------------- P8 Telegram formatting (notify.py) ----------------
def test_notify_signal_card_and_lifecycle():
    from app.autoscalp import notify
    sig = {"decision": "BUY_PE", "direction": "BEARISH", "signal_type": "SUPPORT_BREAKDOWN",
           "sr_side": "SUPPORT", "sr_level": 24085, "strike": 24100, "tradingsymbol": "NIFTY24100PE",
           "entry": 95.0, "stop_loss": 83.0, "target_1": 116.0, "target_2": 128.0,
           "signal_score": 63.0, "probability": 0.58, "confidence": "MEDIUM",
           "regime": "TRENDING_DOWN", "mtf_alignment": -30, "rr": 1.6, "ev": 6.0,
           "support_strength": 60, "resistance_strength": 62}
    card = notify.signal_card(sig, symbol="NIFTY", index_ltp=24095)
    assert "IDADDY AI SIGNAL" in card and "NIFTY PE 24100" in card
    assert "Probability:\n58%" in card and "Confidence:\nMEDIUM" in card
    assert "no live order" in card
    lc = notify.lifecycle("TARGET", {"underlying": "NIFTY", "option_type": "PE", "strike": 24100,
                                     "entry": 95.0, "exit_price": 116.0, "pnl": 21.0, "result": "WIN"})
    assert "AUTO-SCALP TARGET" in lc and "P&L 21.0" in lc and "(WIN)" in lc


def test_notify_push_is_non_blocking_on_failure():
    from app.autoscalp import notify
    calls = []
    assert notify.push(lambda t: (_ for _ in ()).throw(RuntimeError("tg down")), "x") is False
    assert notify.push(lambda t: calls.append(t), "hello") is True and calls == ["hello"]
    assert notify.push(None, "x") is False
