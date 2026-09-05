"""
Order-flow module Phase 1 -- Volume Profile + Market Profile (TPO).
Pure-function math is tested against hand-computed expectations; the
market_hub read helper is tested against a throwaway market_history.db.
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.orderflow import profile as P   # noqa: E402


def _bar(bs, o, h, l, c, v):
    return {"bar_start": bs, "o": o, "h": h, "l": l, "c": c, "v": v}


# ---------------------------------------------------------------- volume_profile
def test_volume_profile_single_bar_distributes_evenly():
    # one bar, range 100..104, tick 1 -> bins centred 100.5,101.5,102.5,103.5
    # (floor=100, n_bins = idx(104)+1 = 4+1 = 5 -> 100.5..104.5). volume 1000
    # spread across the 5 touched bins = 200 each.
    out = P.volume_profile([_bar("2026-09-04T04:00:00Z", 100, 104, 100, 103, 1000)],
                           symbol="X", tick_size=1.0)
    assert out["status"] == "OK"
    assert out["method"] == "OHLCV_RANGE_DISTRIBUTION"
    vols = [b["volume"] for b in out["bins"]]
    assert vols == [200.0, 200.0, 200.0, 200.0, 200.0]
    assert out["total_volume"] == 1000.0
    assert out["session_high"] == 104 and out["session_low"] == 100


def test_volume_profile_poc_is_the_heaviest_bin():
    bars = [
        _bar("2026-09-04T04:00:00Z", 100, 101, 100, 100.5, 100),   # bins 100.5,101.5 -> 50 each
        _bar("2026-09-04T04:05:00Z", 100.5, 100.9, 100.1, 100.6, 900),  # all in bin 100.5 -> 900
    ]
    out = P.volume_profile(bars, symbol="X", tick_size=1.0)
    # bin 100.5 total = 50 + 900 = 950 ; bin 101.5 = 50 -> POC = 100.5
    assert out["poc"] == 100.5


def test_volume_profile_value_area_70pct():
    # narrow bars (l==h) so each bar's whole volume lands in ONE bin -- makes
    # the bin distribution exactly 10 / 80 / 10 at prices 100.5 / 101.5 / 102.5.
    # total 100, target 70. POC bin (80) already >= 70 -> VA is just the POC.
    bars = [
        _bar("2026-09-04T04:00:00Z", 100.5, 100.5, 100.5, 100.5, 10),
        _bar("2026-09-04T04:05:00Z", 101.5, 101.5, 101.5, 101.5, 80),
        _bar("2026-09-04T04:10:00Z", 102.5, 102.5, 102.5, 102.5, 10),
    ]
    out = P.volume_profile(bars, symbol="X", tick_size=1.0)
    assert out["poc"] == 101.5
    assert out["vah"] == 101.5 and out["val"] == 101.5


def test_volume_profile_value_area_expands_to_higher_side():
    # single-bin bars -> bin volumes exactly 5 / 50 / 30 / 5 (total 90, target
    # 63). POC=101.5(50). next step: up=30 vs down=5 -> add up (102.5),
    # acc=80 >= 63. VA = 101.5 .. 102.5
    bars = [
        _bar("2026-09-04T04:00:00Z", 100.5, 100.5, 100.5, 100.5, 5),
        _bar("2026-09-04T04:05:00Z", 101.5, 101.5, 101.5, 101.5, 50),
        _bar("2026-09-04T04:10:00Z", 102.5, 102.5, 102.5, 102.5, 30),
        _bar("2026-09-04T04:15:00Z", 103.5, 103.5, 103.5, 103.5, 5),
    ]
    out = P.volume_profile(bars, symbol="X", tick_size=1.0)
    assert out["poc"] == 101.5
    assert out["val"] == 101.5 and out["vah"] == 102.5


def test_volume_profile_zero_volume_bars_still_give_poc_no_crash():
    bars = [_bar("2026-09-04T04:00:00Z", 100, 102, 100, 101, 0),
            _bar("2026-09-04T04:05:00Z", 100, 102, 100, 101, None)]
    out = P.volume_profile(bars, symbol="X", tick_size=1.0)
    assert out["status"] == "OK"
    assert out["total_volume"] == 0.0
    assert out["poc"] is not None            # falls back to first bin, no exception


def test_volume_profile_no_usable_bars():
    assert P.volume_profile([], symbol="X")["status"] == "NO_DATA"
    assert P.volume_profile([_bar("t", 1, 1, 2, 1, 5)], symbol="X")["status"] == "NO_DATA"  # h<l


# ---------------------------------------------------------------- market_profile
def test_market_profile_tpo_counts_brackets_touching_each_price():
    # two 30-min brackets. bracket A (04:00-04:29): l..h = 100 .. 101.9 -> bins
    # idx(100)=0 .. idx(101.9)=1 -> prices 100.5, 101.5.
    # bracket B (04:30-04:59): l..h = 101 .. 102.9 -> bins 1..2 -> 101.5, 102.5.
    bars = [
        _bar("2026-09-04T04:00:00Z", 100, 101.9, 100, 101, 10),
        _bar("2026-09-04T04:10:00Z", 100, 101, 100, 100.5, 10),
        _bar("2026-09-04T04:35:00Z", 101, 102.9, 101, 102, 10),
    ]
    out = P.market_profile(bars, symbol="X", tick_size=1.0, tpo_minutes=30)
    assert out["status"] == "OK"
    assert out["n_brackets"] == 2
    by_price = {b["price"]: b["tpo"] for b in out["bins"]}
    assert by_price[100.5] == 1     # only bracket A
    assert by_price[101.5] == 2     # both
    assert by_price[102.5] == 1     # only bracket B
    assert out["poc"] == 101.5


def test_market_profile_single_prints_are_tpo_1_prices():
    bars = [
        _bar("2026-09-04T04:00:00Z", 100, 104.9, 100, 102, 10),   # bracket A: 100..104.9 -> bins 0..4
        _bar("2026-09-04T04:35:00Z", 101, 101.9, 101, 101.5, 10),  # bracket B: 101..101.9 -> bin 1 only
    ]
    out = P.market_profile(bars, symbol="X", tick_size=1.0, tpo_minutes=30)
    # only bracket A touched these -> tpo==1: 100.5, 102.5, 103.5, 104.5
    assert set(out["single_prints"]) == {100.5, 102.5, 103.5, 104.5}
    assert 101.5 not in out["single_prints"]   # both brackets touched bin 1


def test_market_profile_letters_assigned_per_bracket():
    bars = [
        _bar("2026-09-04T04:00:00Z", 100, 101, 100, 100.5, 10),
        _bar("2026-09-04T04:35:00Z", 100, 101, 100, 100.5, 10),
        _bar("2026-09-04T05:05:00Z", 100, 101, 100, 100.5, 10),
    ]
    out = P.market_profile(bars, symbol="X", tick_size=1.0, tpo_minutes=30)
    b = out["bins"][0]
    assert b["letters"] == "ABC"       # 3 brackets, all touched this one price bin
    assert b["tpo"] == 3


# ---------------------------------------------------------------- market_hub.session_bars
def test_session_bars_prefers_future_over_index(monkeypatch, tmp_path):
    db = tmp_path / "mh.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE market_candles (symbol TEXT, kind TEXT, tf TEXT, "
        "session_date_ist TEXT, bar_start TEXT, o REAL, h REAL, l REAL, c REAL, v REAL);")
    con.execute("INSERT INTO market_candles VALUES ('NIFTY','INDEX','5m','2026-09-04',"
                "'2026-09-04T04:00:00Z',100,101,99,100,NULL)")
    con.execute("INSERT INTO market_candles VALUES ('NIFTY','FUTURE','5m','2026-09-04',"
                "'2026-09-04T04:00:00Z',100,101,99,100,12345)")
    con.commit(); con.close()

    import app.market_hub as mh
    monkeypatch.setattr(mh, "_HDB", str(db))
    rows = mh.session_bars("NIFTY", "2026-09-04", tf="5m")
    assert len(rows) == 1
    assert rows[0]["v"] == 12345      # FUTURE row, not the volumeless INDEX row


def test_session_bars_empty_when_nothing_captured(monkeypatch, tmp_path):
    db = tmp_path / "mh2.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE market_candles (symbol TEXT, kind TEXT, tf TEXT, "
                "session_date_ist TEXT, bar_start TEXT, o REAL, h REAL, l REAL, c REAL, v REAL)")
    con.commit(); con.close()
    import app.market_hub as mh
    monkeypatch.setattr(mh, "_HDB", str(db))
    assert mh.session_bars("NIFTY", "2026-09-04") == []


# ---------------------------------------------------------------- service layer
def test_service_profile_no_data(monkeypatch):
    import app.orderflow.service as svc
    monkeypatch.setattr(svc.market_hub, "session_bars", lambda sym, d, tf="5m": [])
    out = svc.profile("NIFTY", "2026-09-04")
    assert out["status"] == "NO_DATA"
    assert out["bar_count"] == 0


def test_service_profile_composite_merges_sessions(monkeypatch):
    import app.orderflow.service as svc
    calls = []
    def _bars(sym, d, tf="5m"):
        calls.append(d)
        return [_bar(f"{d}T04:00:00Z", 100, 102, 100, 101, 500)]
    monkeypatch.setattr(svc.market_hub, "session_bars", _bars)
    svc._CACHE.clear()
    out = svc.profile("NIFTY", "2026-09-03,2026-09-04", which="both")
    assert out["composite"] is True
    assert calls == ["2026-09-03", "2026-09-04"]
    assert out["bar_count"] == 2
    assert out["volume_profile"]["status"] == "OK"
    assert out["market_profile"]["status"] == "OK"
