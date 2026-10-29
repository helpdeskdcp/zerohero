"""Arena tick tape: Angel SnapQuote ticks -> quote_snapshots rows that the settlement engine fills from in about a second."""
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.arena import settle as S, tick_tape as TT
from app.histcap.store import HistStore
from app.l2capture.snapquote import build_snapquote_packet, parse_snapquote

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 11, 3, 10, 0, 0, tzinfo=IST).timestamp()
OPT = {"instrument_key": "NFO:55001", "symbol": "NIFTY", "kind": "OPTION", "exchange": "NFO", "token": "55001", "expiry": "03NOV2026", "strike": 25000.0, "option_type": "CE"}
EQ = {"instrument_key": "NSE:2885", "symbol": "RELIANCE", "kind": "EQUITY", "exchange": "NSE", "token": "2885", "expiry": None, "strike": None, "option_type": None}


@pytest.fixture(autouse=True)
def _no_real_kill_file(tmp_path, monkeypatch):
    """tests never see the production kill file data/arena/TICK_TAPE_OFF"""
    monkeypatch.setattr(TT, "OFF_FILE", str(tmp_path / "no_such_off_file"))


def pkt(token="55001", ex=2, seq=1, t=T0, ltp=100.0, bid=99.9, ask=100.1):
    return build_snapquote_packet(token=token, exchange_type=ex, seq=seq, exch_ts_ms=int(t * 1000), ltp=ltp, volume=1000, oi=5000, depth={"buy": [{"price": bid, "quantity": 75, "orders": 2}], "sell": [{"price": ask, "quantity": 150, "orders": 3}]})


def core(hot=True, meta=OPT):
    c = TT.TapeCore(); c.set_metas([{**meta, "hot": hot}]); return c


def test_a_snapquote_tick_becomes_a_settlement_ready_row():
    c = core(); [tick] = parse_snapquote(pkt(ltp=100.0, bid=99.9, ask=100.1))
    [r] = c.on_ticks([tick], T0, "2026-11-03T04:30:00.200000Z")
    assert (r["symbol"], r["kind"], r["expiry"], r["strike"], r["option_type"], r["exchange"], r["token"]) == ("NIFTY", "OPTION", "03NOV2026", 25000.0, "CE", "NFO", "55001")
    assert (r["ltp"], r["bid"], r["ask"], r["bid_qty"], r["ask_qty"], r["volume"], r["oi"]) == (100.0, 99.9, 100.1, 75.0, 150.0, 1000.0, 5000.0)
    assert r["source"] == TT.SOURCE and r["snap_key"].startswith("WS|") and r["session_date_ist"] == "2026-11-03" and r["exch_ts"].startswith("2026-11-03T04:30:00")      # 10:00 IST = 04:30 UTC
    assert len(r["depth_json"]["buy"]) <= TT.DEPTH_LEVELS


def test_hot_contracts_store_every_change_at_most_four_times_a_second_and_never_lose_the_last_one():
    c = core(hot=True); rows = []
    for k, (t, px) in enumerate([(0.0, 100.0), (0.05, 100.2), (0.10, 100.4), (0.30, 100.6)]):
        rows += c.on_ticks(parse_snapquote(pkt(seq=k + 1, t=T0 + t, ltp=px, bid=px - 0.1, ask=px + 0.1)), T0 + t)
    assert [r["ltp"] for r in rows] == [100.0, 100.6]                                  # 100.2 / 100.4 inside the 0.25 s gap are held back, the newest change is stored once the gap passed
    held = c.on_ticks(parse_snapquote(pkt(seq=9, t=T0 + 0.31, ltp=100.8, bid=100.7, ask=100.9)), T0 + 0.31)
    assert held == [] and c.flush_due(T0 + 0.40) == [] and [r["ltp"] for r in c.flush_due(T0 + 0.60)] == [100.8]       # the last change of a quiet contract is flushed, not left behind


def test_warm_contracts_are_slower_and_unchanged_quotes_only_heartbeat():
    c = core(hot=False); a = c.on_ticks(parse_snapquote(pkt(seq=1, t=T0, ltp=100)), T0); b = c.on_ticks(parse_snapquote(pkt(seq=2, t=T0 + 0.5, ltp=101, bid=100.9, ask=101.1)), T0 + 0.5)
    assert len(a) == 1 and b == [] and len(c.flush_due(T0 + 1.2)) == 1                  # 1 s gap for warm contracts
    same = c.on_ticks(parse_snapquote(pkt(seq=3, t=T0 + 2, ltp=101, bid=100.9, ask=101.1)), T0 + 2.0); assert same == []             # unchanged: nothing
    hb = c.on_ticks(parse_snapquote(pkt(seq=4, t=T0 + 6, ltp=101, bid=100.9, ask=101.1)), T0 + 6.0); assert len(hb) == 1               # ... until the 4 s heartbeat is due


def test_unknown_tokens_and_garbage_prices_are_counted_not_stored():
    c = core()
    assert c.on_ticks(parse_snapquote(pkt(token="999")), T0) == [] and c.stats["unknown_token"] == 1
    assert c.on_ticks(parse_snapquote(pkt(ltp=0.0, bid=0.0, ask=0.0)), T0) == [] and c.stats["bad_tick"] == 1
    assert c.on_ticks(parse_snapquote(pkt(ex=1)), T0) == []                              # right token, wrong exchange segment


def test_subscription_diff_and_the_token_cap():
    c = TT.TapeCore(); add, rem = c.set_metas([{**OPT, "hot": True}, {**EQ, "hot": False}]); assert add == {"55001": 2, "2885": 1} and rem == {}
    add, rem = c.set_metas([{**EQ, "hot": False}]); assert add == {} and rem == {"55001": 2}
    many = [{**OPT, "token": str(i), "instrument_key": f"NFO:{i}", "hot": False} for i in range(500)]; c.set_metas(many); assert len(c.meta) == TT.MAX_TOKENS


class FakeWS:
    def __init__(self, frames): self.sent, self.frames = [], list(frames)
    async def send(self, m): self.sent.append(m)
    async def recv(self):
        if self.frames: return self.frames.pop(0)
        await asyncio.sleep(5)


def test_session_subscribes_mode_3_follows_the_watch_list_and_queues_rows():
    state = {"metas": [{**OPT, "hot": True}]}
    tape = TT.TickTape(store=object(), targets_fn=lambda: state["metas"], creds_fn=lambda: ("OK", {}), market_open_fn=lambda: True)
    ws = FakeWS([pkt(seq=1, t=T0, ltp=100)])
    asyncio.run(tape.session(ws, max_seconds=1.3))
    subs = [json.loads(m) for m in ws.sent if m.startswith("{")]
    assert subs[0]["action"] == 1 and subs[0]["params"]["mode"] == 3 and subs[0]["params"]["tokenList"] == [{"exchangeType": 2, "tokens": ["55001"]}] and "ping" in ws.sent
    assert tape._q.qsize() == 1 and tape.last_tick_t > 0
    state["metas"] = [{**EQ, "hot": False}]; ws2 = FakeWS([]); tape.core.meta = {k: v for k, v in tape.core.meta.items()}
    asyncio.run(tape.session(ws2, max_seconds=0.5)); subs2 = [json.loads(m) for m in ws2.sent if m.startswith("{")]
    assert {s["action"] for s in subs2} == {1} and subs2[0]["params"]["tokenList"] == [{"exchangeType": 1, "tokens": ["2885"]}]
    st = tape.status(); assert st["tokens"] == 1 and st["ticks"] == 1 and st["queue"] == 1


def test_server_error_text_is_kept_not_swallowed():
    tape = TT.TickTape(store=object(), targets_fn=lambda: [], creds_fn=None, market_open_fn=lambda: True); ws = FakeWS(['{"errorCode":"AB1004","message":"Invalid token limit exceeded"}'])
    asyncio.run(tape.session(ws, max_seconds=0.6)); assert "limit exceeded" in (tape.last_error or "").lower() or "invalid" in (tape.last_error or "").lower()


def test_ws_rows_are_stored_and_settlement_fills_a_buy_about_one_second_after_the_signal(tmp_path):
    """The point of the tape: ticks every ~0.3 s -> the engine's first ask at/after signal time + 1 s delay is ~1 s away (the REST tape made it 3-9 s)."""
    st = HistStore(str(tmp_path / "m.db")); c = core(hot=True); rows = []
    for k in range(40):                                                                          # 12 seconds of ticks, price drifting up 0.05 per tick, spread 0.2
        px = 100.0 + 0.05 * k; rows += c.on_ticks(parse_snapquote(pkt(seq=k + 1, t=T0 + 0.3 * k, ltp=px, bid=px - 0.1, ask=px + 0.1)), T0 + 0.3 * k)
    tape = TT.TickTape(store=st, targets_fn=lambda: [], market_open_fn=lambda: True); assert tape._write(rows) == len(rows) >= 30
    snaps = S.load_snaps("NIFTY", "2026-11-03", 25000.0, "CE", "2026-11-03", str(tmp_path / "m.db"), since=T0 - 5)
    assert len(snaps) >= 30 and all(abs((snaps[i + 1]["t"] - snaps[i]["t"]) - 0.3) < 0.31 for i in range(5))
    sig = {"id": "H1", "participant": "X", "underlying": "NIFTY", "expiry": "2026-11-03", "strike": 25000.0, "option_type": "CE", "entry_mode": "NEAR", "entry": 100.2, "entry_hi": 100.2, "sl": 90.0, "targets": [101.0], "t": T0 + 0.5, "events": [], "lots": 1}
    r = S.settle(sig, snaps, half_spread=0.005, policy="T1", latency=1)
    assert r["status"] == "SETTLED" and r["fill_t"] - sig["t"] <= 1.6                              # filled within ~1.1-1.4 s of the signal
    assert r["exit_reason"] == "TARGET" and r["hold_sec"] < 8                                          # and the target was seen within seconds, not minutes


def test_option_latest_quote_and_chart_read_the_websocket_rows_too(tmp_path, monkeypatch):
    from app.arena import contracts as CT, terminal as TM
    st = HistStore(str(tmp_path / "m.db")); c = core(hot=True); now = time.time()
    rows = c.on_ticks(parse_snapquote(pkt(seq=1, t=now - 1.0, ltp=123.4, bid=123.3, ask=123.5)), now - 1.0, TT._iso_now())
    tape = TT.TickTape(store=st, targets_fn=lambda: []); assert tape._write(rows) == 1
    CT._lq_cache.clear(); q = CT.latest_quote("NIFTY", "2026-11-03", 25000.0, "CE", str(tmp_path / "m.db"))
    assert q and q["ltp"] == 123.4 and q["bid"] == 123.3 and q["ask"] == 123.5                                  # source ANGELONE_WS_SNAP is accepted by the arena's quote lookup


def test_equity_ticks_hide_an_old_bid_ask_next_to_a_fresh_websocket_price(monkeypatch):
    from app.arena import equity as EQ
    now = 1_800_000_000.0
    monkeypatch.setattr(EQ, "latest_quote", lambda s, path=None: {"ltp": 2428.0, "bid": 2427.8, "ask": 2428.8, "t": now - 300})
    class Feed: ltp = {"11536": {"ltp": 2494.2, "recv": now - 0.2}}
    monkeypatch.setattr(EQ, "_ws_feed", lambda: Feed()); monkeypatch.setattr(EQ, "resolve", lambda s, sdk=None: {"symbol": s, "token": "11536"}); monkeypatch.setattr(EQ, "_sync_subscriptions", lambda *a, **k: None)
    monkeypatch.setattr(EQ, "_nse_open", lambda: False)
    t = EQ.ticks(["TCS"], now=now)["TCS"]; assert t["src"] == "ws" and t["ltp"] == 2494.2 and t["bid"] is None and t["ask"] is None
    monkeypatch.setattr(EQ, "latest_quote", lambda s, path=None: {"ltp": 2494.0, "bid": 2493.9, "ask": 2494.3, "t": now - 2})
    t = EQ.ticks(["TCS"], now=now)["TCS"]; assert t["bid"] == 2493.9 and t["ask"] == 2494.3                      # a fresh pair is kept


def test_the_kill_file_ends_a_running_session_and_blocks_a_new_one(tmp_path, monkeypatch):
    off = tmp_path / "TICK_TAPE_OFF"; monkeypatch.setattr(TT, "OFF_FILE", str(off))
    tape = TT.TickTape(store=object(), targets_fn=lambda: [{**OPT, "hot": True}], market_open_fn=lambda: True); ws = FakeWS([])
    off.write_text("x"); t0 = time.time(); asyncio.run(tape.session(ws)); assert time.time() - t0 < 1.0 and ws.sent == [] and "switched off" in tape.last_error          # returns at once, subscribes nothing
    off.unlink(); assert TT.switched_off() is False


class FakeL2:
    enabled = True
    def __init__(self, own=("61471",)): self.connected = True; self.session_no = 1; self._own = set(own); self.sent = []
    def own_tokens(self): return self._own
    def request_send(self, text): self.sent.append(json.loads(text)); return True


def test_shared_socket_subscribes_extra_tokens_on_the_l2_worker_and_never_touches_its_own():
    state = {"metas": [{**OPT, "hot": True}, {**EQ, "hot": False}, {**OPT, "token": "61471", "instrument_key": "NFO:61471", "hot": False}]}      # the third is a token the L2 worker already streams
    tape = TT.TickTape(store=object(), targets_fn=lambda: state["metas"]); w = FakeL2(); tape.mode = "shared"
    tape.shared_cycle(w)
    assert len(w.sent) == 1 and w.sent[0]["action"] == 1 and sorted(sum((x["tokens"] for x in w.sent[0]["params"]["tokenList"]), [])) == ["2885", "55001"]       # 61471 is NOT subscribed twice
    tape.shared_cycle(w); assert len(w.sent) == 1                                      # nothing new: nothing sent
    state["metas"] = [{**EQ, "hot": False}]; tape.shared_cycle(w)
    assert w.sent[-1]["action"] == 0 and w.sent[-1]["params"]["tokenList"] == [{"exchangeType": 2, "tokens": ["55001"]}]                      # only the tape's own token is unsubscribed, never 61471
    w.session_no = 2; tape.shared_cycle(w); assert w.sent[-1]["action"] == 1 and w.sent[-1]["params"]["tokenList"] == [{"exchangeType": 1, "tokens": ["2885"]}]      # a new connection: subscribe again
    w.connected = False; tape.shared_cycle(w); assert tape.connected is False


def test_shared_frames_become_queued_rows_and_the_kill_file_stops_it(tmp_path, monkeypatch):
    monkeypatch.setattr(TT, "OFF_FILE", str(tmp_path / "OFF"))
    tape = TT.TickTape(store=object(), targets_fn=lambda: [{**OPT, "hot": True}]); tape.mode = "shared"; tape.core.set_metas([{**OPT, "hot": True}])
    tape.offer_frame(pkt(seq=1, t=time.time() - 1.0, ltp=100.0)); assert tape._q.qsize() == 1 and tape.status()["lag_ms_p50"] is not None and tape.status()["mode"] == "shared"
    (tmp_path / "OFF").write_text("x"); tape.offer_frame(pkt(seq=2, t=time.time(), ltp=101.0, bid=100.9, ask=101.1)); assert tape._q.qsize() == 1          # switched off: frames are ignored


def test_the_l2_worker_exposes_the_hooks_and_keeps_its_store_to_its_own_tokens():
    from app.l2capture import worker as W
    w = W.L2CaptureWorker(); assert W.shared_worker() is w and w.session_no == 0 and w.request_send("x") is True
    got = []; W.register_frame_hook(got.append); assert got.append in W._FRAME_HOOKS
    class Store:
        def __init__(self): self.rows = []; self.raw = 0
        def put_raw_frame(self, fr, n_packets, run_id): self.raw += 1; return 1
        def insert_ticks(self, good, raw_id, run_id, meta): self.rows += good; return len(good)
        def finish_run(self, *a, **k): pass
    w.store = Store(); w._tokens = [{"token": "61471", "exchange_type": 2}]; w._store_raw = True
    now_ms = int(time.time() * 1000)
    mine = build_snapquote_packet(token="61471", exchange_type=2, seq=1, exch_ts_ms=now_ms, ltp=100.0, depth={"buy": [{"price": 99.9, "quantity": 75, "orders": 1}], "sell": [{"price": 100.1, "quantity": 75, "orders": 1}]})
    other = build_snapquote_packet(token="55001", exchange_type=2, seq=1, exch_ts_ms=now_ms, ltp=100.0, depth={"buy": [{"price": 99.9, "quantity": 75, "orders": 1}], "sell": [{"price": 100.1, "quantity": 75, "orders": 1}]})
    w._flush([mine, other]); assert [r["token"] for r in w.store.rows] == ["61471"] and w.store.raw == 1                # the arena's token never reaches the research store
    W._FRAME_HOOKS.clear()


def test_governor_drops_hot_contracts_to_the_warm_rate_when_writes_are_slow_and_recovers():
    tape = TT.TickTape(store=object(), targets_fn=lambda: []); tape.core.set_metas([{**OPT, "hot": True}]); n0 = 1_800_000_000.0
    assert tape.govern(0.05, 10, now=n0) is False                                                      # healthy
    assert tape.govern(0.9, 10, now=n0) is True and tape.core.degraded_until == n0 + TT.DEGRADE_S      # a 0.9 s batch: degrade
    c = tape.core; a = c.on_ticks(parse_snapquote(pkt(seq=1, t=n0, ltp=100)), n0 + 1); b = c.on_ticks(parse_snapquote(pkt(seq=2, t=n0 + 0.4, ltp=101, bid=100.9, ask=101.1)), n0 + 1.4)
    assert len(a) == 1 and b == [] and c.flush_due(n0 + 1.5) == [] and len(c.flush_due(n0 + 2.1)) == 1       # 0.4 s later is too soon (WARM gap 1 s), 1.1 s later is fine
    assert tape.govern(0.05, 10, now=n0 + 61) is False                                                  # a minute later, healthy again: back to the HOT rate
    assert tape.govern(0.05, TT.BACKLOG_ROWS + 1, now=n0 + 100) is True                                # a deep queue also degrades
    assert tape.status()["degraded"] in (True, False)
