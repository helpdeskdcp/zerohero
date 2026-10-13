"""Renko shadow log: observation-only, causal, fail-safe (never affects the trade)."""
import asyncio
import datetime as dt
import sqlite3

import numpy as np
import pytest

from app.autoscalp import renko_shadow as RS
from tests.test_autoscalp import _runner
from tests.test_autoscalp_final_signal_gate import _sig

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _ep(day, minute):
    return int(dt.datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=IST).timestamp())


def _series(trend: float, days=4, entry_minute=700):
    """days x 375 one-minute bars ending at `entry_minute` of the last day; `trend` points/bar drift on the last day."""
    out = {k: [] for k in "tohlc"}
    px = 20000.0
    d0 = dt.date(2026, 3, 2)
    for di in range(days):
        day = d0 + dt.timedelta(days=di)
        last = di == days - 1
        for m in range(555, (entry_minute if last else 930)):
            o = px
            px = px + (trend if last else (3.0 if m % 2 else -3.0))
            out["t"].append(_ep(day, m)); out["o"].append(o); out["c"].append(px)
            out["h"].append(max(o, px) + 1.0); out["l"].append(min(o, px) - 1.0)
    return {k: np.asarray(v, np.int64 if k == "t" else float) for k, v in out.items()} | {"v": np.zeros(len(out["t"]))}, \
        dt.datetime.fromtimestamp(_ep(d0 + dt.timedelta(days=days - 1), entry_minute), tz=dt.timezone.utc).isoformat()


def test_uptrend_is_aligned_with_ce_and_not_with_pe(tmp_path):
    bars, entry_ts = _series(trend=4.0)
    prov = lambda *a, **k: bars  # noqa: E731
    ce = RS.log_entry(trade_id="t1", signal_id="s", symbol="NIFTY", option_type="CE", entry_ts=entry_ts,
                      underlying_ltp=1.0, bars_provider=prov, db_path=str(tmp_path / "s.db"))
    pe = RS.log_entry(trade_id="t2", signal_id="s", symbol="NIFTY", option_type="PE", entry_ts=entry_ts,
                      underlying_ltp=1.0, bars_provider=prov, db_path=str(tmp_path / "s.db"))
    assert ce["status"] == "OK" and ce["renko_dir"] == 1 and ce["renko_run"] >= 3
    assert ce["aligned"] == 1 and ce["aligned_run3"] == 1
    assert pe["aligned"] == 0 and pe["aligned_run3"] == 0 and pe["trade_dir"] == -1
    assert ce["bar_lag_sec"] == pytest.approx(0.0)                       # last complete bar ends exactly at entry
    rows = sqlite3.connect(tmp_path / "s.db").execute("SELECT trade_id, aligned_run3 FROM renko_shadow_entries ORDER BY trade_id").fetchall()
    assert rows == [("t1", 1), ("t2", 0)]


def test_unsupported_symbol_and_bad_inputs_are_logged_not_raised(tmp_path):
    db = str(tmp_path / "s.db")
    a = RS.log_entry(trade_id="x", signal_id="s", symbol="COALINDIA", option_type="PE",
                     entry_ts="2026-03-05T05:00:00+00:00", underlying_ltp=1.0, db_path=db)
    assert a["status"] == "UNSUPPORTED"
    b = RS.log_entry(trade_id="y", signal_id="s", symbol="NIFTY", option_type="XX",
                     entry_ts="2026-03-05T05:00:00+00:00", underlying_ltp=1.0, db_path=db)
    assert b["status"] == "UNSUPPORTED"

    def boom(*a, **k):
        raise RuntimeError("db locked")
    c = RS.log_entry(trade_id="z", signal_id="s", symbol="NIFTY", option_type="CE", entry_ts="2026-03-05T05:00:00+00:00",
                     underlying_ltp=1.0, bars_provider=boom, db_path=db)
    assert c["status"] == "DATA_ERROR" and "db locked" in c["reason"]
    d = RS.log_entry(trade_id="w", signal_id="s", symbol="NIFTY", option_type="CE", entry_ts="2026-03-05T05:00:00+00:00",
                     underlying_ltp=1.0, bars_provider=lambda *a, **k: {"t": np.array([], np.int64), "o": np.array([]),
                                                                         "h": np.array([]), "l": np.array([]),
                                                                         "c": np.array([]), "v": np.array([])}, db_path=db)
    assert d["status"] == "INSUFFICIENT_BARS"


def test_log_entry_never_raises_even_if_the_database_path_is_unusable():
    out = RS.log_entry(trade_id="q", signal_id="s", symbol="COALINDIA", option_type="CE",
                       entry_ts="2026-03-05T05:00:00+00:00", underlying_ltp=1.0, db_path="/proc/nope/x.db")
    assert out["status"] == "LOG_ERROR"


def test_write_is_idempotent_per_trade_id(tmp_path):
    db = str(tmp_path / "s.db")
    for _ in range(3):
        RS.log_entry(trade_id="same", signal_id="s", symbol="COALINDIA", option_type="CE",
                     entry_ts="2026-03-05T05:00:00+00:00", underlying_ltp=1.0, db_path=db)
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM renko_shadow_entries").fetchone()[0] == 1


def test_recent_bars_excludes_the_incomplete_minute_and_dedupes(tmp_path):
    p = str(tmp_path / "h.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE market_candles (symbol TEXT, kind TEXT, tf TEXT, bar_start TEXT, o REAL, h REAL, l REAL, c REAL)")
    rows = [("NIFTY", "INDEX", "1m", "2026-03-05T05:00:00+00:00", 1, 2, 0, 1),
            ("NIFTY", "INDEX", "1m", "2026-03-05T05:01:00Z", 1, 2, 0, 1),
            ("NIFTY", "INDEX", "1m", "2026-03-05T05:01:00+00:00", 9, 9, 9, 9),       # duplicate of the 05:01 bar
            ("NIFTY", "INDEX", "1m", "2026-03-05T05:02:00+00:00", 1, 2, 0, 1)]       # still forming at 05:02:30
    c.executemany("INSERT INTO market_candles VALUES (?,?,?,?,?,?,?,?)", rows); c.commit(); c.close()
    until = dt.datetime(2026, 3, 5, 5, 2, 30, tzinfo=dt.timezone.utc).timestamp()
    b = RS.recent_bars("NIFTY", "INDEX", until, db_path=p)
    assert len(b["t"]) == 2                                                          # 05:00 and 05:01 only


def test_kill_switch_prevents_any_thread(monkeypatch):
    started = []
    monkeypatch.setattr(RS.threading, "Thread", lambda *a, **k: started.append(1))
    monkeypatch.setenv("CHANAKYA_RENKO_SHADOW", "0")
    RS.log_entry_async(trade_id="k")
    assert started == []


def test_runner_calls_the_hook_with_the_trade_and_never_blocks_the_trade(fresh_db, monkeypatch):
    calls = []
    monkeypatch.setattr(RS, "log_entry_async", lambda **kw: calls.append(kw))
    r, feed = _runner(monkeypatch, _sig())
    r.arm()
    asyncio.run(r.tick_once())
    trades = fresh_db.list_trades(strategy="AUTOSCALP")
    assert trades and calls and calls[0]["symbol"] == "NIFTY" and calls[0]["option_type"] == "PE"
    assert calls[0]["trade_id"] == trades[0]["trade_id"]


def test_runner_trade_is_unaffected_when_the_hook_raises(fresh_db, monkeypatch):
    def boom(**kw):
        raise RuntimeError("shadow exploded")
    monkeypatch.setattr(RS, "log_entry_async", boom)
    r, feed = _runner(monkeypatch, _sig())
    r.arm()
    asyncio.run(r.tick_once())
    assert fresh_db.list_trades(strategy="AUTOSCALP") != []


def test_hook_is_inert_under_pytest_unless_a_test_db_is_set(monkeypatch):
    started = []
    monkeypatch.setattr(RS.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: started.append(1)})())
    monkeypatch.delenv("CHANAKYA_RENKO_SHADOW", raising=False)
    monkeypatch.delenv("CHANAKYA_RENKO_SHADOW_DB", raising=False)
    assert RS.enabled() is False
    RS.log_entry_async(trade_id="p")
    assert started == []
    monkeypatch.setenv("CHANAKYA_RENKO_SHADOW_DB", "/tmp/never-written.db")
    assert RS.enabled() is True


# ================================================================== tick-level stream
def _tick_bars(n_extra=0):
    bars, entry_ts = _series(trend=4.0)
    if n_extra:                                              # append more complete bars after the first 'now'
        last = int(bars["t"][-1])
        px = float(bars["c"][-1])
        add = {k: [] for k in "tohlc"}
        for i in range(n_extra):
            o = px
            px += 4.0
            add["t"].append(last + 60 * (i + 1)); add["o"].append(o); add["c"].append(px)
            add["h"].append(px + 1); add["l"].append(o - 1)
        bars = {k: np.concatenate([bars[k], np.asarray(add[k], bars[k].dtype)]) for k in "tohlc"} | {"v": np.zeros(len(bars["t"]) + n_extra)}
    return bars, dt.datetime.fromisoformat(entry_ts).timestamp() + 60 * n_extra


def test_init_schema_is_idempotent_and_creates_a_real_zero_row_database(tmp_path):
    db = str(tmp_path / "s.db")
    assert RS.init_schema(db) is True and RS.init_schema(db) is True
    c = sqlite3.connect(db)
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"renko_shadow_entries", "renko_shadow_ticks"} <= names
    assert c.execute("SELECT COUNT(*) FROM renko_shadow_ticks").fetchone()[0] == 0
    assert RS.status(db)["ticks"] == 0 and RS.status(db)["entries"] == 0


def test_db_path_resolution_default_and_env_override(monkeypatch, tmp_path):
    monkeypatch.delenv("CHANAKYA_RENKO_SHADOW_DB", raising=False)
    assert RS._db_path().endswith("/backend/data/renko_shadow.db")
    monkeypatch.setenv("CHANAKYA_RENKO_SHADOW_DB", str(tmp_path / "x.db"))
    assert RS._db_path() == str(tmp_path / "x.db")


def test_valid_tick_is_persisted_read_back_and_duplicates_are_ignored(tmp_path):
    db = str(tmp_path / "s.db")
    bars, now = _tick_bars()
    prov = lambda s, k, u: bars  # noqa: E731
    a = RS.observe_tick(symbol="NIFTY", tick_price=20500.0, decision="NO_TRADE", reason="NSE closed", regime="X",
                        bars_provider=prov, db_path=db, now_epoch=now)
    b = RS.observe_tick(symbol="NIFTY", tick_price=20501.0, decision="NO_TRADE", reason="again",
                        bars_provider=prov, db_path=db, now_epoch=now + 20)          # same completed bar
    assert a["status"] == "OK" and a["inserted"] is True and b["inserted"] is False
    row = sqlite3.connect(db).execute("SELECT symbol, renko_dir, renko_run, decision, no_trade_reason, regime, k, bar_lag_sec "
                                      "FROM renko_shadow_ticks").fetchall()
    assert row == [("NIFTY", 1, a["renko_run"], "NO_TRADE", "NSE closed", "X", 0.12, 0.0)] and a["renko_run"] >= 3
    bars2, now2 = _tick_bars(n_extra=2)                                                  # two newer completed bars
    c = RS.observe_tick(symbol="NIFTY", bars_provider=lambda s, k, u: bars2, db_path=db, now_epoch=now2)
    assert c["inserted"] is True
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM renko_shadow_ticks").fetchone()[0] == 2
    assert RS.status(db)["ticks"] == 2


def test_nothing_observed_means_no_row_and_no_file(tmp_path):
    db = str(tmp_path / "never.db")
    empty = {k: np.array([], np.int64 if k == "t" else float) for k in "tohlcv"}
    assert RS.observe_tick(symbol="NIFTY", bars_provider=lambda s, k, u: empty, db_path=db, now_epoch=1.8e9)["status"] == "INSUFFICIENT_BARS"
    assert RS.observe_tick(symbol="COALINDIA", db_path=db, now_epoch=1.8e9)["status"] == "UNSUPPORTED"
    import os
    assert not os.path.exists(db)                                                        # nothing fabricated, no placeholder file
    assert RS.status(db)["db_exists"] is False


def test_write_failure_is_reported_never_raised(tmp_path):
    bars, now = _tick_bars()
    before = RS._state["write_errors"]
    out = RS.observe_tick(symbol="NIFTY", bars_provider=lambda s, k, u: bars, db_path="/proc/nope/x.db", now_epoch=now)
    assert out["status"] == "LOG_ERROR" and RS._state["write_errors"] == before + 1 and RS._state["last_error"]


def test_async_observer_is_inert_under_pytest_and_does_not_pile_up(monkeypatch):
    started = []
    monkeypatch.setattr(RS.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: started.append(1)})())
    monkeypatch.delenv("CHANAKYA_RENKO_SHADOW_DB", raising=False)
    RS.observe_tick_async(symbol="NIFTY")
    assert started == []                                                                  # pytest guard
    monkeypatch.setenv("CHANAKYA_RENKO_SHADOW_DB", "/tmp/never.db")
    RS.observe_tick_async(symbol="NIFTY"); RS.observe_tick_async(symbol="NIFTY")
    assert started == [1]                                                                 # second call skipped: one in-flight per symbol
    RS._inflight.discard("NIFTY")


def test_runner_tick_calls_observer_with_the_decision_and_fabricates_no_trade(fresh_db, monkeypatch):
    calls = []
    monkeypatch.setattr(RS, "observe_tick_async", lambda **kw: calls.append(kw))
    r, feed = _runner(monkeypatch, {"decision": "NO_TRADE", "reason": "regime blocked", "regime": "UNSTABLE"})
    r.arm()
    asyncio.run(r.tick_once())
    assert calls and calls[0]["symbol"] == "NIFTY" and calls[0]["decision"] == "NO_TRADE" and calls[0]["regime"] == "UNSTABLE"
    assert fresh_db.list_trades(strategy="AUTOSCALP") == []                               # observation never creates a trade


def test_runner_decision_unchanged_when_observer_raises_and_status_has_renko_block(fresh_db, monkeypatch):
    def boom(**kw):
        raise RuntimeError("shadow tick exploded")
    monkeypatch.setattr(RS, "observe_tick_async", boom)
    r, feed = _runner(monkeypatch, _sig())
    r.arm()
    asyncio.run(r.tick_once())
    assert fresh_db.list_trades(strategy="AUTOSCALP") != []                               # BUY_PE still opened as before
    assert "renko_shadow" in r.status() and r.status()["live_trading"] is False


def test_runner_start_initializes_schema_only_when_enabled(monkeypatch):
    called = []
    monkeypatch.setattr(RS, "init_schema", lambda *a, **k: called.append(1) or True)
    monkeypatch.setattr(RS, "enabled", lambda: True)
    from app.autoscalp import runner as ascr

    async def go():
        r = ascr.AutoScalpRunner()
        r.start()
        await r.stop()
    asyncio.run(go())
    assert called == [1]


# ================================================================== offline analysis
def test_forward_bps_uses_next_open_and_signs_by_renko_direction():
    from app.autoscalp import renko_shadow_analysis as A
    t0 = 1_800_000_000
    iso = lambda e: dt.datetime.fromtimestamp(e, tz=dt.timezone.utc).isoformat()  # noqa: E731
    bars = {t0 + 60 * k: (100.0 + k, 100.0 + k + 0.5) for k in range(1, 40)}
    tick = {"symbol": "NIFTY", "last_bar_ts": iso(t0), "renko_dir": 1, "renko_run": 3}
    f = A.forward_bps(tick, bars, 15)
    assert f == pytest.approx(((100.0 + 15 + 0.5) / (100.0 + 1) - 1) * 1e4)             # entry = OPEN of t0+60, exit = CLOSE at t0+15m
    assert A.forward_bps({**tick, "renko_dir": -1}, bars, 15) == pytest.approx(-f)
    assert A.forward_bps({**tick, "renko_dir": 0}, bars, 15) is None
    assert A.forward_bps(tick, {k: v for k, v in bars.items() if k < t0 + 600}, 15) is None   # horizon not observed


def test_analysis_refuses_conclusions_on_small_samples():
    from app.autoscalp import renko_shadow_analysis as A
    t0 = 1_800_000_000
    iso = lambda e: dt.datetime.fromtimestamp(e, tz=dt.timezone.utc).isoformat()  # noqa: E731
    bars = {t0 + 60 * k: (100.0, 100.0) for k in range(1, 200)}
    ticks = [{"symbol": "NIFTY", "last_bar_ts": iso(t0 + 60 * k), "renko_dir": 1, "renko_run": min(k, 4)} for k in range(1, 4)]
    r = A.analyse(ticks, {"NIFTY": bars}, horizons=(15,))[15]
    assert r["verdict"].startswith("INSUFFICIENT SAMPLE") and "diff_ci95_bps" not in r
