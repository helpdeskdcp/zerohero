"""Fix #1 of the afternoon-degradation report: the greeks_engine CYCLE pass is session/expiry scoped, deadline-bounded and runs OFF the histcap capture thread."""
import sqlite3, threading, time
from datetime import datetime, timezone

import pytest

from app.greeks_engine.engine import GreeksEngine
from app.histcap import worker as W
from app.histcap.store import HistStore
from test_greeks_engine import _seed, _iso


@pytest.fixture
def hist_db(tmp_path):
    p = str(tmp_path / "mh.db"); HistStore(p); return p


# ---------------- engine scoping ----------------
def test_scoped_pass_matches_unscoped_for_the_same_expiry(hist_db, tmp_path):
    ts = _iso(datetime.now(timezone.utc)); _seed(hist_db, ts=ts)
    a = GreeksEngine(hist_db).run_once("NIFTY", expiries=["09SEP2026"], mode="CYCLE", session_date_ist="2026-09-02")
    other = str(tmp_path / "o.db"); HistStore(other); _seed(other, ts=ts)
    b = GreeksEngine(other).run_once("NIFTY", expiries=["09SEP2026"], mode="CYCLE")
    assert a["snapshots_written"] == b["snapshots_written"] == 1
    sa, sb = GreeksEngine(hist_db).latest("NIFTY"), GreeksEngine(other).latest("NIFTY")
    for k in ("as_of_ts", "underlying_price", "n_pairs_used", "ce_delta_exp", "pe_delta_exp", "pcr_oi", "oi_weighted_iv", "quality"): assert sa[k] == sb[k], k


def test_other_session_rows_are_not_read(hist_db):
    _seed(hist_db, ts=_iso(datetime.now(timezone.utc)))                                      # rows are tagged session 2026-09-02
    r = GreeksEngine(hist_db).run_once("NIFTY", expiries=["09SEP2026"], mode="CYCLE", session_date_ist="2026-09-03")
    assert r["snapshots_written"] == 0 and r["errors"] and "no OK greek rows" in r["errors"][0]["reason"]


def test_only_the_requested_expiry_is_processed(hist_db):
    now = datetime.now(timezone.utc); _seed(hist_db, ts=_iso(now)); _seed(hist_db, ts=_iso(now.replace(microsecond=0)).replace("Z", "") + "Z", expiry="02SEP2026")
    r = GreeksEngine(hist_db).run_once("NIFTY", expiries=["09SEP2026"], mode="CYCLE", session_date_ist="2026-09-02")
    assert r["expiries"] == ["09SEP2026"]
    # and without explicit expiries + a session, discovery is limited to that session day (never the whole history)
    e = GreeksEngine(hist_db)
    with e._conn() as c:
        assert set(e._expiries(c, "NIFTY", "2026-09-02")) == {"09SEP2026", "02SEP2026"} and e._expiries(c, "NIFTY", "2026-09-03") == []


def test_deadline_aborts_a_runaway_statement_and_still_closes_the_run(hist_db, monkeypatch):
    _seed(hist_db, ts=_iso(datetime.now(timezone.utc))); eng = GreeksEngine(hist_db)
    def slow(conn, *a, **k):
        conn.execute("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<2000000000) SELECT max(x) FROM c").fetchone()
    monkeypatch.setattr(eng, "_target_ts", slow)
    t = time.time(); r = eng.run_once("NIFTY", expiries=["09SEP2026"], mode="CYCLE", session_date_ist="2026-09-02", deadline_sec=0.5); dt = time.time() - t
    assert dt < 5 and r["snapshots_written"] == 0 and "interrupted" in r["errors"][0]["error"]
    row = eng.runs(1)[0]; assert row["ended_ts"] is not None                                                             # the closing UPDATE was not interrupted
    monkeypatch.undo()                                                                                                  # engine still usable afterwards (real _target_ts)
    assert eng.run_once("NIFTY", expiries=["09SEP2026"], mode="CYCLE", session_date_ist="2026-09-02")["snapshots_written"] == 1


# ---------------- worker offload ----------------
class _FakeStore:
    def start_run(self, *a, **k): return 1
    def finish_run(self, *a, **k): pass
    def summary(self): return {}


def _worker(monkeypatch):
    monkeypatch.setenv("CHANAKYA_HIST_SYMBOLS", "NIFTY"); monkeypatch.setenv("CHANAKYA_HIST_DHAN_CAPTURE", "0")
    return W.CaptureWorker(sdk_provider=lambda: object(), store=_FakeStore(), dhan_auth_checker=lambda: False)


class _SlowEngine:
    def __init__(self): self.release = threading.Event(); self.started = threading.Event(); self.calls = []
    def run_once(self, underlying, **kw):
        self.calls.append((underlying, kw)); self.started.set(); self.release.wait(10); return {"snapshots_written": 1, "errors": []}


def _wait(cond, secs=5):
    t = time.time()
    while time.time() - t < secs:
        if cond(): return True
        time.sleep(0.01)
    return False


def test_kick_does_not_block_is_single_flight_and_rate_limited(monkeypatch):
    w = _worker(monkeypatch); eng = _SlowEngine()
    import app.greeks_engine.engine as GE
    monkeypatch.setattr(GE, "greeks_engine", lambda: eng)
    t = time.time(); assert w._kick_greeks_engine("27OCT2026") is True; assert time.time() - t < 0.5            # returns at once although the pass blocks
    assert eng.started.wait(5) and w._ge_running is True
    assert w._kick_greeks_engine("27OCT2026") is False and w._ge_skipped == 1                                   # single flight
    eng.release.set(); assert _wait(lambda: not w._ge_running)
    assert w._kick_greeks_engine("27OCT2026") is False                                                          # within the 60 s minimum interval
    monkeypatch.setenv("CHANAKYA_HIST_GREEKS_ENGINE_MIN_SEC", "0"); eng.release.set()
    assert w._kick_greeks_engine("27OCT2026") is True and _wait(lambda: not w._ge_running)
    und, kw = eng.calls[0]
    assert und == "NIFTY" and kw["expiries"] == ["27OCT2026"] and kw["mode"] == "CYCLE" and kw["deadline_sec"] == 120.0
    assert kw["session_date_ist"] == datetime.now(timezone.utc).astimezone(W.timezone(W.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
    assert w.status.__func__ is not None and w._ge_last["snapshots"] == 1


def test_engine_failure_is_isolated_and_releases_the_flight(monkeypatch):
    w = _worker(monkeypatch)
    class Boom:
        def run_once(self, *a, **k): raise RuntimeError("x")
    import app.greeks_engine.engine as GE
    monkeypatch.setattr(GE, "greeks_engine", lambda: Boom())
    assert w._kick_greeks_engine("27OCT2026") is True and _wait(lambda: not w._ge_running)
    assert "greeks_engine: RuntimeError" in w.last_error and w._ge_last["error"] == "RuntimeError"


def _stub_cycle(w, monkeypatch, greeks, expiry):
    monkeypatch.setattr(w, "_auth_ok", lambda: True)
    monkeypatch.setattr(w, "_refs", lambda sdk, sym: {"tokens_meta": [{}], "opt_expiry": expiry})
    monkeypatch.setattr(w, "_capture_quotes", lambda *a, **k: None); monkeypatch.setattr(w, "_capture_candles", lambda *a, **k: None)
    monkeypatch.setattr(w, "_capture_dhan_all", lambda *a, **k: None)
    monkeypatch.setattr(w, "_capture_greeks", lambda conn, sdk, sym, refs, recv, rid, counts, *a: counts.__setitem__("greeks", greeks))


def test_run_once_hands_off_the_current_expiry_and_only_when_greeks_were_written(monkeypatch):
    w = _worker(monkeypatch); calls = []; monkeypatch.setattr(w, "_kick_greeks_engine", lambda e: calls.append(e))
    _stub_cycle(w, monkeypatch, 5, "27OCT2026"); w.run_once("POLL", do_candles=False); assert calls == ["27OCT2026"]
    _stub_cycle(w, monkeypatch, 0, "27OCT2026"); w.run_once("POLL", do_candles=False); assert calls == ["27OCT2026"]          # no greeks written -> no pass
    _stub_cycle(w, monkeypatch, 5, None); w.run_once("POLL", do_candles=False); assert calls == ["27OCT2026"]                  # no resolved expiry -> no pass


def test_run_once_returns_while_the_engine_pass_is_still_running(monkeypatch):
    w = _worker(monkeypatch); eng = _SlowEngine()
    import app.greeks_engine.engine as GE
    monkeypatch.setattr(GE, "greeks_engine", lambda: eng); _stub_cycle(w, monkeypatch, 5, "27OCT2026")
    t = time.time(); w.run_once("POLL", do_candles=False); dt = time.time() - t
    assert dt < 2 and eng.started.wait(5) and w._ge_running                                                                      # cycle finished, engine pass still in flight
    eng.release.set(); assert _wait(lambda: not w._ge_running)
