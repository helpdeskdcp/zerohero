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


def _ohlc_1m(n, base=1_800_000_000, px=24000.0, drift=0.4):
    rows = []
    for i in range(n):
        o = px + i * drift
        c = o + drift
        rows.append([base + i * 60, round(o, 2), round(max(o, c) + 1.5, 2),
                     round(min(o, c) - 1.5, 2), round(c, 2), 1000])
    return rows


def test_aggregator_seed_from_ohlc_builds_higher_tfs():
    a = CandleAggregator()
    rows = _ohlc_1m(150)                       # 2.5h of 1m bars
    n = a.seed_from_ohlc(rows)
    assert n == 150
    snap = a.snapshot(now_epoch=rows[-1][0] + 120)
    assert len(snap["1m"]) >= 140
    assert len(snap["5m"]) >= 20               # <-- the engine's gate now clears on a fresh start
    b0 = snap["5m"][0]
    assert b0["h"] >= b0["o"] and b0["h"] >= b0["c"] and b0["l"] <= b0["o"]
    assert a.last_price == rows[-1][4]


def test_aggregator_seed_is_noop_after_a_live_tick():
    a = CandleAggregator()
    a.add_tick(1_800_100_000, 24100.0)        # live tick first
    assert a.seed_from_ohlc(_ohlc_1m(150)) == 0
    assert a.last_price == 24100.0            # history was not rewritten


def test_runner_seeds_index_agg_from_broker_on_start(fresh_db, monkeypatch):
    # No manual pre-fill of the aggregator: prove tick_once() backfills it from
    # the broker so a (re)start does not blind the engine for ~100 minutes.
    now = 1_800_000_000.0 + 150 * 60
    rows = _ohlc_1m(150, base=1_800_000_000)
    calls = []

    def fake_fetch(market, symbol, exchange, symboltoken, interval, fromdate,
                   todate, timeframe=None, instrument=None):
        calls.append((symbol, timeframe))
        return {"candles": [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                            for r in rows], "data_status": "OK"}

    import app.connectors.angelone as _ang
    monkeypatch.setattr(_ang, "fetch_candles", fake_fetch)   # runner imports it lazily

    captured = {}

    def fake_decide(bars, *a, **k):
        captured["n5"] = len(bars.get("5m") or [])
        return {"decision": "NO_TRADE", "signal_type": "NONE", "reason": "x"}

    monkeypatch.setattr(ascr, "decide_from_context", fake_decide)

    feed = FakeFeed({"99926000": 24099.0})
    r = ascr.AutoScalpRunner(feed=feed, chain_provider=lambda *a, **k: [],
                             telegram_fn=lambda *a: None, now_fn=lambda: now)
    r.set_config({"safeguards": {"allow_weekend": True, "session_start_hhmm": "00:00",
                                 "daily_cutoff_hhmm": "23:59"}})
    r.arm()
    asyncio.run(r.tick_once())

    assert ("NIFTY", "1m") in calls                     # broker candles were fetched
    assert len(r._aggs["NIFTY"].snapshot(now_epoch=now)["5m"]) >= 20
    assert captured.get("n5", 0) >= 20                   # _evaluate actually ran the engine


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


def test_safeguards_rejects_premium_too_rich_vs_spot(fresh_db):
    sg = Safeguards({"session_start_hhmm": "00:00", "daily_cutoff_hhmm": "23:59",
                     "max_option_premium_pct": 8.0})
    # NG: 67.50 premium on a ~280 future = 24% of spot -> blocked
    ok, why = _base_check(sg, option_premium=67.5, underlying_price=280.0)
    assert ok is False and "% of spot" in why
    # a normal ATM leg (15 on 280 = 5.4%) passes
    assert _base_check(sg, option_premium=15.0, underlying_price=280.0)[0] is True
    # no spot available -> the pct gate is skipped, not fail-closed
    assert _base_check(sg, option_premium=67.5, underlying_price=None)[0] is True
    # absolute cap still works independently
    sg2 = Safeguards({"session_start_hhmm": "00:00", "daily_cutoff_hhmm": "23:59",
                      "max_option_premium_pct": None, "max_option_premium": 50.0})
    assert _base_check(sg2, option_premium=67.5, underlying_price=280.0)[0] is False
    assert _base_check(sg2, option_premium=40.0, underlying_price=280.0)[0] is True


# ---------------- Runner wiring ----------------
class FakeFeed:
    def __init__(self, marks):
        self.marks = marks                 # token -> ltp
        self.subscribed = []
        self.connected = True

    def subscribe(self, toks, *, owner="default"):
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
    r.set_config({"symbols": ["NIFTY"],           # single-symbol runner mechanics
                  "safeguards": {"allow_weekend": True, "session_start_hhmm": "00:00",
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
    # close now backfills the full outcome record, not just points
    assert sigs[0]["points"] is not None
    assert sigs[0]["r_multiple"] is not None and sigs[0]["r_multiple"] > 0   # win -> positive R
    assert sigs[0]["holding_sec"] is not None and sigs[0]["holding_sec"] >= 0


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


def test_symbol_meta_and_default_watchlist():
    # NG / Crude ship in the default watchlist and carry MCX metadata
    assert set(ascr.DEFAULT_CONFIG["symbols"]) >= {"NIFTY", "NATURALGAS", "CRUDEOIL"}
    assert ascr._sym_meta("NATURALGAS") == {"exchange": "MCX", "strike_step": 2.5}
    assert ascr._sym_meta("CRUDEOIL")["exchange"] == "MCX"
    assert ascr._sym_meta("NIFTY") == {"exchange": "NSE", "strike_step": 50.0}
    assert ascr._sym_meta("WHATEVER")["exchange"] == "NSE"        # graceful default


def test_underlying_ref_nse_uses_registry(fresh_db):
    ref = ascr._underlying_ref("NIFTY")
    assert ref["exchange"] == "NSE" and ref["token"] == "99926000"


def test_equity_fno_strike_step_is_inferred_from_master(fresh_db):
    # an F&O stock not in _SYMBOL_META gets its real option strike grid so it
    # scalps correctly when the operator adds it to `symbols`
    m = ascr._sym_meta("RELIANCE")
    assert m["exchange"] == "NSE" and 1.0 <= m["strike_step"] <= 100.0
    # an unresolvable symbol falls back safely, never crashes
    assert ascr._sym_meta("NOTATICKER")["strike_step"] == 50.0


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


# --------------------------------------------------------------------------- #
# expiry-day (0-DTE) trading + "zero to hero" lottery leg
# --------------------------------------------------------------------------- #
def _expiry_chain_provider(expiry, far_prem=6.0):
    """chain_provider stub: the ATM window returns just the ATM row; a wide
    window (>= otm_strikes+1) additionally returns the far-OTM 24300 row."""
    def _leg(tok, ts):
        return {"ltp": None, "token": tok, "tradingsymbol": ts, "expiry": expiry,
                "oi": 1, "oi_chg": 0, "vol_delta": 0, "exchange_type": 2}
    atm_row = {"strike": 24100,
               "ce": {**_leg("CE24100", "NIFTY24100CE"), "ltp": 130.0},
               "pe": {**_leg("PE24100", "NIFTY24100PE"), "ltp": 95.0}}
    far_row = {"strike": 24300,
               "ce": {**_leg("CE24300", "NIFTY24300CE"), "ltp": far_prem},
               "pe": {**_leg("PE24300", "NIFTY24300PE"), "ltp": far_prem}}

    def cp(sym, atm, window, market="NSE", emode="AUTO"):
        return [atm_row, far_row] if window >= 5 else [atm_row]
    return cp


_EXP_SIG = {"decision": "BUY_CE", "signal_type": "RESISTANCE_BREAKOUT", "direction": "BULLISH",
            "strike": 24100, "token": "CE24100", "tradingsymbol": "NIFTY24100CE",
            "entry": 130.0, "stop_loss": 115.0, "target_1": 160.0, "target_2": 180.0,
            "trailing_stop": 10.0, "max_hold_sec": 480, "probability": 0.62,
            "confidence": "HIGH", "ev": 8.0, "rr": 1.8, "regime": "TRENDING_UP",
            "mtf_alignment": 35.0, "signal_score": 70.0, "component_scores": {"x": 1},
            "reason": "test", "support": 24050, "resistance": 24120,
            "support_strength": 60, "resistance_strength": 62, "sr_level": 24120,
            "sr_side": "RESISTANCE", "atr": 12.0, "vwap": 24090.0}


def _pin_ist(monkeypatch, hhmm="10:00"):
    """Freeze IST wall-clock to today's real date at hhmm (keeps expiry-date
    matching real while making the time-of-day gates deterministic)."""
    h, m = (int(x) for x in hhmm.split(":"))
    fixed = ascr._ist_now().replace(hour=h, minute=m, second=0, microsecond=0)
    monkeypatch.setattr(ascr, "_ist_now", lambda: fixed)
    return fixed


def test_zero_to_hero_opens_far_otm_lottery_on_expiry_day(fresh_db, monkeypatch):
    r, feed = _runner(monkeypatch, dict(_EXP_SIG))
    _pin_ist(monkeypatch, "10:00")
    today = ascr._ist_now().strftime("%d%b%Y").upper()
    r.chain_provider = _expiry_chain_provider(today, far_prem=6.0)
    feed.marks["CE24300"] = 6.0
    r.arm()
    asyncio.run(r.tick_once())

    core = fresh_db.list_trades(strategy="AUTOSCALP")
    zth = fresh_db.list_trades(strategy="AUTOSCALP-ZTH")
    assert len(core) == 1 and core[0]["status"] == "OPEN"
    assert len(zth) == 1
    z = zth[0]
    assert z["option_type"] == "CE" and z["strike"] == 24300 and z["symboltoken"] == "CE24300"
    assert z["entry"] == 6.0 and z["target_1"] == 18.0 and z["stop_loss"] == 3.0
    assert z["max_hold_sec"] and z["max_hold_sec"] > 0
    # a second tick must not open a second ZTH (max_per_day)
    asyncio.run(r.tick_once())
    assert len(fresh_db.list_trades(strategy="AUTOSCALP-ZTH")) == 1


def test_zth_skipped_when_not_expiry_day(fresh_db, monkeypatch):
    r, feed = _runner(monkeypatch, dict(_EXP_SIG))
    _pin_ist(monkeypatch, "10:00")
    r.chain_provider = _expiry_chain_provider("08SEP2099", far_prem=6.0)
    feed.marks["CE24300"] = 6.0
    r.arm()
    asyncio.run(r.tick_once())
    assert len(fresh_db.list_trades(strategy="AUTOSCALP")) == 1
    assert fresh_db.list_trades(strategy="AUTOSCALP-ZTH") == []


def test_zth_skipped_when_far_premium_too_rich(fresh_db, monkeypatch):
    r, feed = _runner(monkeypatch, dict(_EXP_SIG))
    _pin_ist(monkeypatch, "10:00")
    today = ascr._ist_now().strftime("%d%b%Y").upper()
    r.chain_provider = _expiry_chain_provider(today, far_prem=25.0)   # > zth.max_premium
    r.arm()
    asyncio.run(r.tick_once())
    assert len(fresh_db.list_trades(strategy="AUTOSCALP")) == 1
    assert fresh_db.list_trades(strategy="AUTOSCALP-ZTH") == []


def test_expiry_day_entry_cutoff_blocks_new_scalp(fresh_db, monkeypatch):
    r, feed = _runner(monkeypatch, dict(_EXP_SIG))
    _pin_ist(monkeypatch, "15:00")                                   # past 14:15 cutoff
    today = ascr._ist_now().strftime("%d%b%Y").upper()
    r.chain_provider = _expiry_chain_provider(today, far_prem=6.0)
    r.arm()
    asyncio.run(r.tick_once())
    assert fresh_db.list_trades(strategy="AUTOSCALP") == []
    assert fresh_db.list_trades(strategy="AUTOSCALP-ZTH") == []
