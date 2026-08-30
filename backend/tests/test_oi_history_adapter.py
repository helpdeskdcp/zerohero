"""Read-only oi_history adapter — hermetic tests on a synthetic mini-DB that
mirrors the real `/root/oi_dashboard/oi_history.db` schema (cycles + strikes).
No real DB, no network."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.backtest import oi_history_adapter as ad


# --- minimal real-schema slices -------------------------------------------------
_CYCLES_DDL = """
CREATE TABLE cycles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT, ts TEXT, date TEXT, time TEXT,
  underlying_ltp REAL, atm INTEGER, pcr REAL, max_pain INTEGER,
  bias TEXT, note TEXT,
  signal_action TEXT, signal_strike INTEGER, signal_direction TEXT,
  signal_entry REAL, signal_target REAL, signal_sl REAL, signal_confidence INTEGER,
  signal_tradeable INTEGER
);"""
_STRIKES_DDL = """
CREATE TABLE strikes (
  cycle_id INTEGER, strike INTEGER,
  ce_oi INTEGER, ce_oi_chg INTEGER, ce_vol INTEGER, ce_ltp REAL, ce_chg_pct REAL, ce_signal TEXT,
  pe_oi INTEGER, pe_oi_chg INTEGER, pe_vol INTEGER, pe_ltp REAL, pe_chg_pct REAL, pe_signal TEXT,
  ce_iv REAL, pe_iv REAL, ce_delta REAL, ce_gamma REAL, ce_theta REAL, ce_vega REAL,
  pe_delta REAL, pe_gamma REAL, pe_theta REAL, pe_vega REAL,
  ce_trading_symbol TEXT, ce_token TEXT, pe_trading_symbol TEXT, pe_token TEXT,
  ce_contract_expiry TEXT, pe_contract_expiry TEXT
);"""


@pytest.fixture()
def mini_db(tmp_path, monkeypatch):
    p = tmp_path / "oi_history.db"
    c = sqlite3.connect(p)
    c.executescript(_CYCLES_DDL + _STRIKES_DDL)
    # Two days, symbol NIFTY. Day 1: 3 cycles 1 min apart. Day 2: 2 cycles.
    cycles = [
        # id, symbol, ts, date, time, ltp, atm, pcr, maxpain, bias
        (1, "NIFTY", "2026-08-27T09:15:10", "2026-08-27", "09:15:10", 24000.0, 24000, 1.10, 24000, "NEUTRAL"),
        (2, "NIFTY", "2026-08-27T09:16:20", "2026-08-27", "09:16:20", 24012.0, 24000, 1.12, 24000, "BULLISH"),
        (3, "NIFTY", "2026-08-27T09:17:30", "2026-08-27", "09:17:30", 24025.0, 24000, 1.15, 24050, "BULLISH"),
        (4, "NIFTY", "2026-08-28T09:15:05", "2026-08-28", "09:15:05", 24100.0, 24100, 0.95, 24100, "BEARISH"),
        (5, "NIFTY", "2026-08-28T09:18:40", "2026-08-28", "09:18:40", 24080.0, 24100, 0.92, 24100, "BEARISH"),
        (6, "BANKNIFTY", "2026-08-28T09:15:05", "2026-08-28", "09:15:05", 52000.0, 52000, 1.0, 52000, "NEUTRAL"),
    ]
    for row in cycles:
        c.execute("INSERT INTO cycles (id,symbol,ts,date,time,underlying_ltp,atm,pcr,max_pain,bias) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)", row)
    # strikes: 2 strikes/cycle for NIFTY. ce_vol is CUMULATIVE within a day.
    # (cycle_id, strike, ce_oi, ce_oi_chg, ce_vol, ce_ltp, ce_chg_pct, pe_oi, pe_oi_chg, pe_vol, pe_ltp, ce_iv, ce_delta, ce_token, ce_expiry)
    S = [
        (1, 24000, 100000, 0, 5000, 80.0, 0.0, 90000, 0, 4000, 75.0, 12.0, 0.51, "T1", "2026-09-03"),
        (1, 24050, 60000, 0, 3000, 55.0, 0.0, 120000, 0, 8000, 110.0, 11.0, 0.40, "T2", "2026-09-03"),
        (2, 24000, 100000, 0, 6200, 84.0, 5.0, 90000, 0, 4300, 72.0, None, None, None, None),  # greeks/token missing
        (2, 24050, 61000, 1000, 3600, 58.0, 5.4, 120000, 0, 8100, 108.0, None, None, None, None),
        (3, 24000, 102000, 2000, 9000, 90.0, 12.5, 88000, -2000, 4600, 68.0, 13.5, 0.55, "T1", "2026-09-03"),
        (3, 24050, 61500, 500, 4100, 63.0, 14.5, 121000, 1000, 8300, 104.0, 12.9, 0.44, "T2", "2026-09-03"),
        # Day 2 -> cumulative volume RESETS (smaller numbers again)
        (4, 24100, 50000, 0, 1200, 70.0, 0.0, 52000, 0, 900, 66.0, 14.0, 0.50, "T3", "2026-09-03"),
        (4, 24150, 40000, 0, 800, 48.0, 0.0, 70000, 0, 1500, 95.0, 13.0, 0.40, "T4", "2026-09-03"),
        (5, 24100, 51000, 1000, 2000, 63.0, -10.0, 53000, 1000, 1400, 74.0, 14.2, 0.47, "T3", "2026-09-03"),
        (5, 24150, 40500, 500, 1100, 43.0, -10.4, 71000, 1000, 1900, 101.0, 13.4, 0.38, "T4", "2026-09-03"),
        (6, 52000, 10000, 0, 500, 300.0, 0.0, 11000, 0, 400, 280.0, 15.0, 0.5, "B1", "2026-09-03"),
    ]
    for r in S:
        c.execute(
            "INSERT INTO strikes (cycle_id,strike,ce_oi,ce_oi_chg,ce_vol,ce_ltp,ce_chg_pct,"
            "pe_oi,pe_oi_chg,pe_vol,pe_ltp,ce_iv,ce_delta,ce_token,ce_contract_expiry) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
    c.commit()
    c.close()
    monkeypatch.setenv("OI_HISTORY_DB", str(p))
    return p


def test_is_available_and_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("OI_HISTORY_DB", str(tmp_path / "nope.db"))
    assert ad.is_available() is False
    with pytest.raises(FileNotFoundError):
        list(ad.iter_market_states("NIFTY"))


def test_market_states_chronological_and_grouped(mini_db):
    states = list(ad.iter_market_states("NIFTY"))
    assert len(states) == 5                       # 5 NIFTY cycles (BANKNIFTY excluded)
    assert [s["ts"] for s in states] == sorted(s["ts"] for s in states)   # chronological
    s0 = states[0]
    assert s0["symbol"] == "NIFTY" and s0["index_ltp"] == 24000.0 and s0["atm"] == 24000
    assert s0["pcr"] == 1.10 and s0["bias"] == "NEUTRAL"
    assert [c["strike"] for c in s0["chain"]] == [24000, 24050]
    assert s0["chain"][0]["ce"]["ltp"] == 80.0 and s0["chain"][0]["pe"]["ltp"] == 75.0
    assert s0["_src"]["cycle_id"] == 1


def test_missing_greeks_and_tokens_are_none_not_fabricated(mini_db):
    states = list(ad.iter_market_states("NIFTY"))
    c2 = states[1]["chain"][0]["ce"]              # cycle 2 had NULL greeks/token
    assert c2["iv"] is None and c2["delta"] is None
    assert c2["token"] is None and c2["expiry"] is None
    c3 = states[2]["chain"][0]["ce"]              # cycle 3 had them
    assert c3["iv"] == 13.5 and c3["token"] == "T1"


def test_vol_delta_differences_and_resets_daily(mini_db):
    states = list(ad.iter_market_states("NIFTY"))
    # Day 1, strike 24000 ce_vol: 5000 -> 6200 -> 9000
    d0 = states[0]["chain"][0]["ce"]
    d1 = states[1]["chain"][0]["ce"]
    d2 = states[2]["chain"][0]["ce"]
    assert d0["vol_cum"] == 5000 and d0["vol_delta"] == 0.0     # first sighting -> 0
    assert d1["vol_delta"] == 1200.0                            # 6200 - 5000
    assert d2["vol_delta"] == 2800.0                            # 9000 - 6200
    # Day 2 first cycle (id 4) -> counter reset, delta back to 0
    day2 = states[3]["chain"][0]["ce"]
    assert day2["vol_cum"] == 1200 and day2["vol_delta"] == 0.0
    assert states[4]["chain"][0]["ce"]["vol_delta"] == 800.0    # 2000 - 1200


def test_date_range_filter(mini_db):
    only27 = list(ad.iter_market_states("NIFTY", start="2026-08-27", end="2026-08-27"))
    assert {s["ts"][:10] for s in only27} == {"2026-08-27"} and len(only27) == 3


def test_resample_index_candles_1m_and_3m(mini_db):
    one = ad.resample_candles("NIFTY", "1m", kind="index", start="2026-08-27", end="2026-08-27")
    assert one["source"] == "OI_HISTORY" and one["count"] == 3
    bars = one["candles"]
    assert [b["t"] for b in bars] == [
        "2026-08-27T09:15:00", "2026-08-27T09:16:00", "2026-08-27T09:17:00"]
    assert bars[0]["o"] == bars[0]["c"] == 24000.0
    assert bars[-1]["c"] == 24025.0
    # 3m: all three ticks fall in the 09:15 bucket -> one bar, OHLC spans them
    three = ad.resample_candles("NIFTY", "3m", kind="index", start="2026-08-27", end="2026-08-27")
    assert three["count"] == 1
    b = three["candles"][0]
    assert b["o"] == 24000.0 and b["h"] == 24025.0 and b["l"] == 24000.0 and b["c"] == 24025.0
    # index volume proxy = ATM(24000) CE+PE vol_delta summed over the bucket
    # ce: 0+1200+2800=4000 ; pe: 0+300+300=600  -> 4600
    assert b["v"] == pytest.approx(4600.0)


def test_resample_option_candles_requires_strike(mini_db):
    with pytest.raises(ValueError):
        ad.resample_candles("NIFTY", "1m", kind="option")
    ce = ad.resample_candles("NIFTY", "3m", kind="option", strike=24050, option_type="CE",
                             start="2026-08-27", end="2026-08-27")
    assert ce["count"] == 1 and ce["strike"] == 24050 and ce["option_type"] == "CE"
    b = ce["candles"][0]
    assert b["o"] == 55.0 and b["c"] == 63.0 and b["h"] == 63.0
    # ce_vol 3000 -> 3600 -> 4100 : deltas 0 + 600 + 500 = 1100
    assert b["v"] == pytest.approx(1100.0)


def test_unsupported_timeframe_raises(mini_db):
    with pytest.raises(ValueError):
        ad.resample_candles("NIFTY", "7m", kind="index")


def test_quality_manifest_shape(mini_db):
    m = ad.data_quality_manifest()
    assert m["available"] is True
    assert m["cycles"]["rows"] == 6 and m["cycles"]["date_min"] == "2026-08-27"
    syms = {s["symbol"]: s for s in m["symbols"]}
    assert syms["NIFTY"]["cycles"] == 5 and syms["NIFTY"]["days"] == 2
    assert 0 <= m["strikes"]["null_greeks_pct"] <= 100
    assert m["strikes"]["null_ltp_pct"] == 0.0
    assert any("cumulative" in x for x in m["known_limitations"])


def test_manifest_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("OI_HISTORY_DB", str(tmp_path / "absent.db"))
    m = ad.data_quality_manifest()
    assert m["available"] is False
