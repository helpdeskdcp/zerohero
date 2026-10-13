"""G3: 1-second candles, higher timeframes, bounded persistence and the gate->engine->writer pipeline."""
import datetime as dt
import sqlite3

import pytest

from app.platform.candles import engine_1s as E
from app.platform.candles import timeframes as TF
from app.platform.marketdata import arbiter as AR
from app.platform.marketdata.types import Tick
from app.platform.persistence import buffered_writer as BW

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def ep(h, m, s=0, day=13):
    return dt.datetime(2026, 10, day, h, m, s, tzinfo=IST).timestamp()           # 2026-10-13 is a Tuesday


def tk(t, px, provider="ANGELONE", inst="NSE:1", **kw):
    return Tick(provider, inst, px, t, t + 0.05, **kw)


def run(eng, ticks, flush_at=None):
    out = []
    for t in ticks:
        out += eng.add(t)
    if flush_at is not None:
        out += eng.flush(flush_at)
    return out


# ------------------------------------------------------------------ 1-second candles
def test_ohlc_tickcount_and_close_only_after_watermark():
    e = E.CandleEngine1s()
    t0 = ep(10, 0)
    assert e.add(tk(t0 + 0.1, 100)) == [] and e.add(tk(t0 + 0.5, 103)) == [] and e.add(tk(t0 + 0.9, 99)) == []
    assert e.add(tk(t0 + 1.2, 101)) == []                                        # bucket+1 not yet beyond late tolerance
    out = e.add(tk(t0 + 3.5, 102))
    c = out[0]
    assert (c.o, c.h, c.l, c.c, c.tick_count, c.closed, c.synthetic) == (100, 103, 99, 99, 3, True, False)
    assert c.start_ts == int(t0)


def test_empty_seconds_are_never_fabricated_and_gaps_are_reported():
    e = E.CandleEngine1s(gap_after_sec=5)
    t0 = ep(10, 0)
    out = run(e, [tk(t0 + 0.5, 100), tk(t0 + 10.5, 101)], flush_at=t0 + 20)
    assert [c.start_ts for c in out] == [int(t0), int(t0) + 10]                   # exactly two real candles, none in between
    assert len(e.gaps) == 1 and e.gaps[0].seconds == 10 and e.gaps[0].after_ts == int(t0)


def test_volume_comes_from_cumulative_deltas_and_baselines_are_honest():
    e = E.CandleEngine1s()
    t0 = ep(10, 0)
    out = run(e, [tk(t0 + .1, 100, cum_volume=1000), tk(t0 + 1.1, 100, cum_volume=1010), tk(t0 + 2.1, 100, cum_volume=1025)],
              flush_at=t0 + 10)
    assert out[0].volume == 0.0 and out[0].volume_complete is False               # first tick only establishes the baseline
    assert out[1].volume == 10 and out[1].volume_complete is True
    assert out[2].volume == 15 and out[2].volume_complete is True


def test_counter_reset_and_provider_handover_never_create_negative_or_spiked_volume():
    e = E.CandleEngine1s()
    t0 = ep(10, 0)
    e.add(tk(t0 + .1, 100, cum_volume=90000)); e.add(tk(t0 + 1.1, 100, cum_volume=90100))
    e.add(tk(t0 + 2.1, 100, cum_volume=5))                                         # decrease: new day / reset
    e.add(Tick("DHAN", "NSE:1", 100, t0 + 3.1, t0 + 3.2, cum_volume=70), reset_volume_baseline=True)
    out = e.flush(t0 + 20)
    assert all((c.volume or 0) >= 0 for c in out) and max(c.volume or 0 for c in out) <= 100


def test_provider_without_volume_gives_none_not_zero_and_oi_is_a_snapshot_not_volume():
    e = E.CandleEngine1s()
    t0 = ep(10, 0)
    out = run(e, [tk(t0 + .1, 100, oi=5000), tk(t0 + .6, 101, oi=5050), tk(t0 + 1.1, 102, oi=5100)], flush_at=t0 + 10)
    assert out[0].volume is None and out[1].volume is None
    assert (out[0].oi, out[0].oi_change) == (5050, None) and (out[1].oi, out[1].oi_change) == (5100, 50)


def test_late_tick_within_tolerance_is_merged_after_close_is_dropped_and_counted():
    e = E.CandleEngine1s(late_tolerance_sec=2.0)
    t0 = ep(10, 0)
    e.add(tk(t0 + .2, 100)); e.add(tk(t0 + 1.5, 101))
    e.add(tk(t0 + .8, 105))                                                        # late by <2s: bucket still open
    out = e.add(tk(t0 + 4.0, 102))
    first = [c for c in out if c.start_ts == int(t0)][0]
    assert first.h == 105 and first.tick_count == 2
    assert e.add(tk(t0 + .9, 99)) == [] and e.counters["late_dropped"] == 1


def test_session_gating_nse_vs_mcx_and_weekend():
    e = E.CandleEngine1s()
    assert e.add(tk(ep(8, 0), 100)) == [] and e.counters["outside_session"] == 1   # before 09:15 NSE
    e2 = E.CandleEngine1s()
    out = run(e2, [tk(ep(21, 0, 0), 100, inst="MCX:9"), tk(ep(21, 0, 5), 101, inst="MCX:9")], flush_at=ep(21, 1))
    assert len(out) == 2                                                           # MCX open at 21:00
    sat = dt.datetime(2026, 10, 17, 10, 0, tzinfo=IST).timestamp()
    assert E.make_session_fn()("NSE:1", sat) is False


def test_flush_closes_without_further_ticks():
    e = E.CandleEngine1s()
    t0 = ep(10, 0)
    e.add(tk(t0 + .1, 100))
    assert e.flush(t0 + 1.5) == [] and len(e.flush(t0 + 3.5)) == 1


# ------------------------------------------------------------------ timeframes
def _secs(start, n, px0=100.0):
    e = E.CandleEngine1s()
    out = []
    for i in range(n):
        out += e.add(tk(start + i + .1, px0 + (i % 7), cum_volume=1000 + 5 * i, oi=7000 + i))
    return out + e.flush(start + n + 10)


def test_higher_timeframes_align_to_session_open_and_match_batch_aggregation():
    start = ep(9, 15)
    c1 = _secs(start, 5 * 60 + 10)
    b5 = TF.aggregate(c1, 300)
    assert [dt.datetime.fromtimestamp(c.start_ts, IST).strftime("%H:%M") for c in b5] == ["09:15", "09:20"]
    assert b5[0].tick_count == 300 and b5[0].o == c1[0].o and b5[0].c == c1[299].c
    assert b5[0].h == max(c.h for c in c1[:300]) and b5[0].l == min(c.l for c in c1[:300])
    b60 = TF.aggregate(c1, 3600)
    assert dt.datetime.fromtimestamp(b60[0].start_ts, IST).strftime("%H:%M") == "09:15"
    inc = TF.MultiTF(tfs=(300,))
    closed = []
    for c in c1:
        closed += inc.add(c)
    assert len(closed) == 1 and (closed[0].o, closed[0].h, closed[0].l, closed[0].c, closed[0].tick_count) == \
        (b5[0].o, b5[0].h, b5[0].l, b5[0].c, b5[0].tick_count)
    partial = inc.current("NSE:1", 300)
    assert partial.closed is False and partial.start_ts == b5[1].start_ts


def test_merge_volume_completeness_and_oi_change_are_not_fabricated():
    c1 = _secs(ep(9, 15), 20)
    m = TF.aggregate(c1, 300)[0]
    assert m.volume_complete is False                                              # first candle's baseline is unknown
    assert m.oi == c1[-1].oi and m.oi_change == sum(c.oi_change for c in c1 if c.oi_change is not None)
    none_vol = TF.aggregate(run(E.CandleEngine1s(), [tk(ep(9, 16) + .1, 100), tk(ep(9, 16) + 1.1, 101)], flush_at=ep(9, 17)), 300)[0]
    assert none_vol.volume is None and none_vol.volume_complete is False


# ------------------------------------------------------------------ persistence
@pytest.fixture
def writer(tmp_path):
    return BW.BufferedCandleWriter(str(tmp_path / "t.db"), batch_size=100)


def _candles(n, start=1_800_000_000):
    return [E.Candle("NSE:1", 1, start + i, "ANGELONE", 100, 101, 99, 100.5, 3, volume=10.0, volume_complete=True, oi=5, oi_change=0.0,
                     first_ts=start + i, last_ts=start + i, closed=True) for i in range(n)]


def test_writer_roundtrip_and_idempotent_replay(writer):
    cs = _candles(50)
    for c in cs:
        assert writer.offer(c)
    assert writer.flush_sync() == 50
    for c in cs[:10]:
        writer.offer(c)                                                           # replay: INSERT OR IGNORE
    writer.flush_sync()
    n = sqlite3.connect(writer.path).execute("SELECT COUNT(*), MIN(o), MAX(volume_complete) FROM candles").fetchone()
    assert n == (50, 100.0, 1)


def test_backpressure_rejects_visibly_instead_of_dropping_silently(tmp_path):
    rejected = []
    w = BW.BufferedCandleWriter(str(tmp_path / "t.db"), max_queue=2, on_reject=rejected.append)
    res = [w.offer(c) for c in _candles(4)]
    assert res == [True, True, False, False] and w.counters["rejected_full"] == 2 and len(rejected) == 2
    assert w.status()["queue_depth"] == 2


def test_failed_writes_are_retained_and_recover(tmp_path, monkeypatch):
    w = BW.BufferedCandleWriter(str(tmp_path / "t.db"), batch_size=10)
    for c in _candles(25):
        w.offer(c)
    real = w._conn
    state = {"fail": True}

    def flaky():
        if state["fail"]:
            raise sqlite3.OperationalError("database is locked")
        return real()
    monkeypatch.setattr(w, "_conn", flaky)
    assert w.flush_sync() == 0 and w.counters["write_errors"] >= 1 and "locked" in w.last_error
    state["fail"] = False
    assert w.flush_sync() == 25 and w.counters["written"] == 25 and w.counters["lost_over_cap"] == 0
    assert real().execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 25


def test_loss_only_beyond_the_pending_cap_and_it_is_reported(tmp_path, monkeypatch):
    lost = []
    w = BW.BufferedCandleWriter(str(tmp_path / "t.db"), batch_size=10, max_pending=15, on_reject=lost.append)
    for c in _candles(40):
        w.offer(c)
    monkeypatch.setattr(w, "_conn", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")))
    w.flush_sync(max_batches=50)
    assert w.counters["lost_over_cap"] == len(lost) > 0 or w.status()["pending_batches"] > 0


def test_purge_is_bounded_and_chunked(writer):
    conn = sqlite3.connect(writer.path)
    conn.executemany("INSERT INTO candles VALUES ('X',1,?,'P',1,1,1,1,1,NULL,0,NULL,NULL,0,0)", [(i,) for i in range(12_000)])
    conn.commit(); conn.close()
    assert writer.purge_older_than(10_000, chunk=5000, max_chunks=2) == 10_000    # bounded per call
    assert writer.purge_older_than(10_000, chunk=5000, max_chunks=2) == 0
    assert sqlite3.connect(writer.path).execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 2_000


def test_gap_events_persist_and_thread_writer_drains(tmp_path):
    w = BW.BufferedCandleWriter(str(tmp_path / "t.db"), flush_interval=0.05)
    w.start()
    for c in _candles(30):
        w.offer(c)
    e = E.CandleEngine1s(gap_after_sec=5)
    run(e, [tk(ep(10, 0) + .5, 100), tk(ep(10, 0) + 20.5, 101)], flush_at=ep(10, 1))
    assert w.write_gaps(e.gaps) == 1 and w.write_gaps(e.gaps) == 0                 # idempotent
    w.stop()
    assert sqlite3.connect(w.path).execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 30
    st = w.status()
    assert st["written"] == 30 and st["write_latency_ms_p95"] is not None and st["queue_depth"] == 0


def test_storage_estimate_matches_the_documented_budget():
    est = BW.estimate_storage(5, 5, 14)
    assert est["rows_per_day_max"] == 5 * 22500 + 5 * 52200 and 45 < est["mb_per_day_max"] < 52
    assert est["mb_for_days_max"] < 800                                           # 14-day default retention fits easily


# ------------------------------------------------------------------ end-to-end (gate -> engine -> writer)
def test_pipeline_two_providers_with_handover_has_no_mixing_and_no_volume_spike(tmp_path):
    active = {"p": "ANGELONE"}
    gate = AR.TickGate(lambda: active["p"])
    eng = E.CandleEngine1s()
    w = BW.BufferedCandleWriter(str(tmp_path / "t.db"))
    t0 = ep(10, 0)

    def feed(t, provider, px, cum=None):
        res = gate.accept(Tick(provider, "NSE:1", px, t, t + 0.05, cum_volume=cum))
        if res.accepted:
            for c in eng.add(Tick(provider, "NSE:1", px, t, t + 0.05, cum_volume=cum), reset_volume_baseline=res.reset_volume_baseline):
                w.offer(c)
        return res
    for i in range(5):
        feed(t0 + i + .1, "ANGELONE", 100 + i, cum=1000 + 10 * i)
    feed(t0 + 2.5, "DHAN", 999, cum=9_000_000)                                      # standby provider: must be ignored
    active["p"] = "DHAN"
    assert not feed(t0 + 3.5, "DHAN", 103, cum=9_000_100).accepted                 # overlaps Angel's covered period
    first = feed(t0 + 6.1, "DHAN", 105, cum=9_000_150)
    assert first.accepted and first.reset_volume_baseline
    for i in range(7, 10):
        feed(t0 + i + .1, "DHAN", 105 + i, cum=9_000_150 + 20 * (i - 6))
    for c in eng.flush(t0 + 30):
        w.offer(c)
    w.flush_sync()
    rows = sqlite3.connect(w.path).execute("SELECT provider, volume, h FROM candles ORDER BY start_ts").fetchall()
    assert {r[0] for r in rows} == {"ANGELONE", "DHAN"}
    assert all(r[2] < 500 for r in rows)                                            # the 999 standby tick never reached a candle
    assert max(r[1] or 0 for r in rows) <= 50                                       # no 9,000,000 cumulative-volume spike


def test_identical_trades_are_distinct_unless_near_simultaneous_or_volume_counter_matches():
    g = AR.TickGate(lambda: "ANGELONE", dup_recv_window_sec=0.05)
    a = Tick("ANGELONE", "NSE:1", 100.0, 1000.0, 1000.01, qty=75)
    assert g.accept(a).accepted
    assert g.accept(Tick("ANGELONE", "NSE:1", 100.0, 1000.0, 1000.02, qty=75)).reason == "DUPLICATE"       # redelivered in the same frame
    assert g.accept(Tick("ANGELONE", "NSE:1", 100.0, 1000.0, 1000.60, qty=75)).accepted                    # same second/price/qty, 0.6s later: a real second trade
    v = Tick("ANGELONE", "NSE:1", 101.0, 1001.0, 1001.01, qty=75, cum_volume=5000)
    assert g.accept(v).accepted
    assert g.accept(Tick("ANGELONE", "NSE:1", 101.0, 1001.0, 1009.0, qty=75, cum_volume=5000)).reason == "DUPLICATE"  # counter identical => duplicate at any delay


def test_gap_threshold_is_configurable_per_instrument_and_defaults_are_not_noisy():
    e = E.CandleEngine1s(gap_threshold_fn=lambda inst: 3 if inst == "NSE:LIQ" else 120)
    t0 = ep(10, 0)
    run(e, [tk(t0 + .5, 100, inst="NSE:LIQ"), tk(t0 + 10.5, 100, inst="NSE:LIQ"),
            tk(t0 + .5, 100, inst="NSE:OPT"), tk(t0 + 10.5, 100, inst="NSE:OPT")], flush_at=t0 + 30)
    assert [g.instrument for g in e.gaps] == ["NSE:LIQ"]
    assert E.CandleEngine1s().gap_after == 30


def test_service_persists_gap_events_once(tmp_path):
    from app.platform.marketdata.service import MarketDataService
    w = BW.BufferedCandleWriter(str(tmp_path / "t.db"))
    svc = MarketDataService(writer=w, engine=E.CandleEngine1s(gap_after_sec=5))
    svc.arbiter.active = "ANGELONE"
    t0 = ep(10, 0)
    for t in (t0 + .5, t0 + 20.5):
        svc.on_tick(Tick("ANGELONE", "NSE:1", 100, t, t + .05))
    svc.tick(t0 + 40); svc.tick(t0 + 50)
    assert sqlite3.connect(w.path).execute("SELECT COUNT(*), MAX(seconds) FROM gap_events").fetchone() == (1, 20)
