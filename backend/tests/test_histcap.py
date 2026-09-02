"""
Historical capture worker — schema, normalization, integrity, dedup, retrieval,
and a full mocked capture cycle. No network, no trading logic, no live orders.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.histcap import normalize as N              # noqa: E402
from app.histcap import integrity as IG             # noqa: E402
from app.histcap.store import HistStore             # noqa: E402
from app.histcap.worker import CaptureWorker        # noqa: E402
from app.histcap import worker as WK                # noqa: E402
from app import market_calendar, instruments        # noqa: E402

_IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def store(tmp_path):
    return HistStore(str(tmp_path / "mh.db"))


# ------------------------------------------------------------------ normalize
def test_norm_quote_maps_full_fields_and_marks_missing_null():
    raw = {"status": "OK", "ltp": 278.5, "open": 277.0, "high": 279.9, "low": 276.4, "close": 277.2,
           "tradeVolume": 445000, "opnInterest": 1223750, "changeinOpenInterest": 30250,
           "exchangeTimestamp": "2026-09-02T14:00:00+05:30", "server_timestamp": "2026-09-02T08:30:05Z",
           "netChange": 1.3, "percentChange": 0.47, "lowerCircuit": 250.0, "upperCircuit": 305.0,
           "depth": {"buy": [{"price": 278.4, "quantity": 1250, "orders": 3}],
                     "sell": [{"price": 278.6, "quantity": 2500, "orders": 5}]}}
    meta = {"instrument_key": "MCX:454185", "symbol": "NATURALGAS", "kind": "FUTURE",
            "exchange": "MCX", "token": "454185", "expiry": "24SEP2026"}
    r = N.norm_quote(raw, meta, N.now_utc_iso(), spot_ltp=277.9)
    assert r["ltp"] == 278.5 and r["volume"] == 445000.0 and r["oi"] == 1223750.0
    assert r["oi_change"] == 30250.0 and r["bid"] == 278.4 and r["ask"] == 278.6
    assert r["bid_qty"] == 1250.0 and r["ask_qty"] == 2500.0
    assert r["exch_ts"] == "2026-09-02T08:30:00Z"                  # +05:30 -> UTC
    assert r["basis"] == round(278.5 - 277.9, 4)                   # DERIVED, FUTURE only
    assert r["week52_high"] is None and r["avg_price"] is None     # absent -> NULL, not 0
    assert r["session_date_ist"] == "2026-09-02"


def test_norm_quote_no_basis_for_non_future():
    r = N.norm_quote({"ltp": 100.0}, {"instrument_key": "NSE:1", "symbol": "NIFTY", "kind": "INDEX",
                                      "exchange": "NSE", "token": "1"}, N.now_utc_iso(), spot_ltp=99.0)
    assert r["basis"] is None


def test_to_utc_iso_handles_epoch_ms_iso_and_naive():
    assert N.to_utc_iso(1756800000000).endswith("Z")             # epoch ms
    assert N.to_utc_iso("2026-09-02T14:00:00+05:30") == "2026-09-02T08:30:00Z"
    assert N.to_utc_iso("02-Sep-2026 14:00:00") == "2026-09-02T08:30:00Z"   # IST naive
    assert N.to_utc_iso("") is None and N.to_utc_iso(None) is None


def test_norm_candles_only_emits_closed_bars():
    now = datetime.now(timezone.utc)
    closed = (now - timedelta(minutes=10)).replace(second=0, microsecond=0)
    openb = (now - timedelta(seconds=30)).replace(second=0, microsecond=0)
    raw = [{"timestamp": closed.astimezone(_IST).isoformat(), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
           {"timestamp": openb.astimezone(_IST).isoformat(), "open": 1.5, "high": 3, "low": 1, "close": 2, "volume": 5}]
    meta = {"instrument_key": "MCX:1", "symbol": "NG", "kind": "FUTURE", "exchange": "MCX", "token": "1"}
    out = N.norm_candles(raw, meta, "5m", N.now_utc_iso())
    assert len(out) == 1 and out[0]["c"] == 1.5 and out[0]["v"] == 10.0    # open bar dropped


def test_norm_candles_null_volume_stays_null():
    now = datetime.now(timezone.utc)
    b = (now - timedelta(minutes=20)).replace(second=0, microsecond=0).astimezone(_IST).isoformat()
    out = N.norm_candles([{"timestamp": b, "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": None}],
                         {"instrument_key": "NSE:1", "symbol": "NIFTY", "kind": "INDEX",
                          "exchange": "NSE", "token": "1"}, "5m", N.now_utc_iso())
    assert out and out[0]["v"] is None                            # index -> no volume, not 0


def test_norm_greeks_delegates_and_keeps_pct():
    rows = [{"strikePrice": "278.000000", "optionType": "CE", "delta": "0.51", "gamma": "0.02",
             "theta": "-1.2", "vega": "0.8", "impliedVolatility": "42.5", "tradeVolume": "100"}]
    out = N.norm_greeks(rows, "NATURALGAS", "24SEP2026", "OK", N.now_utc_iso())
    assert out[0]["strike"] == 278.0 and out[0]["option_type"] == "CE"
    assert abs(out[0]["iv"] - 0.425) < 1e-9 and out[0]["iv_pct"] == 42.5
    assert out[0]["broker_status"] == "OK"


# ------------------------------------------------------------------ integrity
def test_candle_check_rejects_impossible_and_flags_soft():
    ok, flags = IG.candle_check(1, 0.5, 2, 1, 10, 100)            # h<l
    assert ok is False and flags == ["HARD:h<l"]
    ok, flags = IG.candle_check(0.4, 2, 0.5, 1, -1, -5)           # o<l, v<0, oi<0
    assert ok is True and set(flags) >= {"o<l", "v<0", "oi<0"}
    ok, flags = IG.candle_check(1, 2, 0.5, 1.5, 10, 100)
    assert ok is True and flags == []


def test_quote_check_crossed_book_and_neg_oi():
    assert "crossed_book" in IG.quote_check(100, 101, 99, 5, 0)
    assert "oi<0" in IG.quote_check(100, 99, 101, -3, 0)


def test_greek_check_flags_iv_oob_without_clamping():
    flags = IG.greek_check(0.5, 0.01, -1, 0.5, 9.9)              # 990% IV
    assert "iv_oob" in flags
    assert "delta_oob" in IG.greek_check(1.4, 0.01, -1, 0.5, 0.3)


# ------------------------------------------------------------------ store: dedup + append-only
def test_put_raw_dedups_by_content_hash(store):
    with store.transaction() as c:
        a = store.put_raw(c, endpoint="x", request={"q": 1}, http_status=200, status="OK",
                          payload={"k": "v"}, server_ts=None, run_id=1)
        b = store.put_raw(c, endpoint="x", request={"q": 1}, http_status=200, status="OK",
                          payload={"k": "v"}, server_ts=None, run_id=1)
    assert a == b
    assert store.summary()["raw"] == 1


def test_write_candles_is_idempotent_on_natural_key(store):
    now = datetime.now(timezone.utc)
    bs = (now - timedelta(minutes=30)).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    row = {"received_ts": N.now_utc_iso(), "instrument_key": "MCX:1", "symbol": "NG", "kind": "FUTURE",
           "exchange": "MCX", "token": "1", "expiry": None, "strike": None, "option_type": None,
           "tf": "5m", "bar_start": bs, "session_date_ist": "2026-09-02",
           "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10, "oi": 100, "oi_change": 0,
           "source": "ANGELONE_CANDLES", "raw_id": None}
    integ = {}
    with store.transaction() as c:
        n1 = store.write_candles(c, [dict(row)], 1, integ)
    with store.transaction() as c:
        n2 = store.write_candles(c, [dict(row), {**row, "c": 99}], 2, integ)   # same key -> ignored
    assert n1 == 1 and n2 == 0
    assert store.summary()["candles"] == 1


def test_write_candles_rejects_hard_bad_row(store):
    bad = {"received_ts": N.now_utc_iso(), "instrument_key": "MCX:1", "symbol": "NG", "kind": "FUTURE",
           "exchange": "MCX", "token": "1", "tf": "5m",
           "bar_start": "2026-09-02T05:00:00Z", "session_date_ist": "2026-09-02",
           "o": 1, "h": 0.4, "l": 2, "c": 1, "v": 10, "oi": 5, "source": "ANGELONE_CANDLES"}
    integ = {"rejected": []}
    with store.transaction() as c:
        n = store.write_candles(c, [bad], 1, integ)
    assert n == 0 and integ["rejected"] and "HARD:h<l" in integ["rejected"][0]["reason"]


# ------------------------------------------------------------------ full cycle (mocked SDK)
class _FakeSDK:
    last_auth = {"status": "OK"}

    def __init__(self, *, greek_status="OK", auth=True):
        self._greek_status, self._auth = greek_status, auth
        self.expiry = (datetime.now(timezone.utc) + timedelta(days=20)).strftime("%d%b%Y").upper()

    def authenticate(self):
        return self._auth

    def resolve_future_contract(self, sym, mode="AUTO"):
        return {"status": "OK", "exchange": "MCX", "token": "454185",
                "underlying": sym.upper(), "expiry": self.expiry}

    def resolve_option_contract(self, sym, expiry, strike, typ):
        return {"status": "OK", "exchange": "MCX", "expiry": self.expiry, "strike": 278.0}

    def _option_universe(self, sym):
        return {"exchange": "MCX", "types": ("OPTFUT",), "quote_ex": "MCX", "spot": 278.0}

    def search_instruments(self, *, symbol=None, exchange=None, instrumenttype=None):
        out = []
        for k in (270.0, 275.0, 278.0, 280.0, 285.0):
            for typ in ("CE", "PE"):
                out.append({"exch_seg": "MCX", "name": symbol.upper(),
                            "symbol": f"{symbol.upper()}{self.expiry}{int(k)}{typ}",
                            "token": f"T{int(k)}{typ}", "instrumenttype": "OPTFUT",
                            "expiry": self.expiry, "strike": str(int(k * 100))})
        return out

    def get_quotes_batch(self, by_ex, mode="FULL"):
        out = {}
        for exch, toks in by_ex.items():
            for t in toks:
                out[str(t)] = {"status": "OK", "ltp": 278.3, "open": 277.0, "high": 279.0, "low": 276.5,
                               "close": 277.1, "tradeVolume": 1000, "opnInterest": 50000,
                               "changeinOpenInterest": 1200, "exchangeTimestamp": "2026-09-02T14:00:00+05:30",
                               "depth": {"buy": [{"price": 278.2, "quantity": 50, "orders": 1}],
                                         "sell": [{"price": 278.4, "quantity": 75, "orders": 2}]},
                               "symboltoken": str(t), "exchange": exch}
        return out

    def get_option_greeks(self, underlying, expiry):
        if self._greek_status != "OK":
            return {"status": self._greek_status, "errorcode": "AB9019", "message": "No Data Available",
                    "rows": [], "source": "ANGELONE_OPTION_GREEK", "http_status": 200,
                    "fetched_at": N.now_utc_iso(), "cache": "MISS"}
        rows = []
        for k in (270.0, 275.0, 278.0, 280.0, 285.0):
            for typ, d in (("CE", 0.6), ("PE", -0.4)):
                rows.append({"strike": k, "option_type": typ, "delta": d, "gamma": 0.02,
                             "theta": -1.1, "vega": 0.9, "iv": 0.43, "iv_pct": 43.0,
                             "trade_volume": 10.0, "source": "ANGELONE_OPTION_GREEK", "status": "OK"})
        return {"status": "OK", "rows": rows, "source": "ANGELONE_OPTION_GREEK", "http_status": 200,
                "fetched_at": N.now_utc_iso(), "cache": "MISS", "errorcode": "", "message": "SUCCESS"}

    def get_candles(self, exchange, token, interval, frm, to):
        now = datetime.now(timezone.utc)
        cs = []
        for i in range(6, 1, -1):
            b = (now - timedelta(minutes=5 * i)).replace(second=0, microsecond=0)
            cs.append({"timestamp": b.astimezone(_IST).isoformat(), "open": 277 + i * 0.1,
                       "high": 278 + i * 0.1, "low": 276 + i * 0.1, "close": 277.5, "volume": 100 * i})
        return {"status": "OK", "data_status": "OK", "candles": cs}


def _worker(store, monkeypatch, sdk):
    monkeypatch.setattr(market_calendar, "status_all",
                        lambda *a, **k: {"segments": {"MCX": "OPEN", "NSE": "CLOSED"}})
    w = CaptureWorker(sdk_provider=lambda: sdk, store=store)
    w.cfg = {**w.cfg, "symbols": ["NATURALGAS"], "chain_window": 2, "tfs": ["5m"], "option_candles": False}
    return w


def test_run_once_captures_quotes_greeks_candles(store, monkeypatch):
    w = _worker(store, monkeypatch, _FakeSDK())
    r = w.run_once("POLL_ONCE", do_candles=True)
    assert r["auth_ok"] is True
    assert r["quotes"] >= 11          # future + 5 strikes x 2 legs
    assert r["greeks"] == 10          # 5 strikes x 2
    assert r["candles"] >= 3          # closed 5m bars for the future
    s = store.summary()
    assert s["quotes"] == r["quotes"] and s["greeks_ok"] == 10 and s["candles"] == r["candles"]
    # a captured option quote has real broker fields + provenance
    q = store.get_quotes("NATURALGAS", kind="OPTION")[0]
    assert q["ltp"] == 278.3 and q["oi"] == 50000.0 and q["bid"] == 278.2 and q["ask"] == 278.4
    assert q["raw_id"] and q["source"] == "ANGELONE_QUOTE_FULL"
    g = store.get_greeks("NATURALGAS")[0]
    assert g["delta"] in (0.6, -0.4) and g["iv"] == 0.43 and g["source"] == "ANGELONE_OPTION_GREEK"


def test_run_once_is_idempotent_second_cycle_adds_nothing(store, monkeypatch):
    sdk = _FakeSDK()
    w = _worker(store, monkeypatch, sdk)
    r1 = w.run_once("POLL_ONCE", do_candles=True)
    before = store.summary()
    r2 = w.run_once("POLL_ONCE", do_candles=True)     # same snap_key + bar_start -> ignored
    after = store.summary()
    assert after["candles"] == before["candles"]       # candles fully deduped
    assert r2["greeks"] == 0                           # same snap_key second
    assert after["quotes"] == before["quotes"]         # same exch_ts snap_key


def test_run_once_without_auth_writes_no_market_rows_but_logs_run(store, monkeypatch):
    w = _worker(store, monkeypatch, _FakeSDK(auth=False))
    r = w.run_once("POLL_ONCE", do_candles=True)
    assert r["auth_ok"] is False and r["quotes"] == 0 and r["greeks"] == 0 and r["candles"] == 0
    runs = store.runs(1)
    assert runs and runs[0]["auth_ok"] == 0 and "credentials unavailable" in (runs[0]["notes"] or "")


def test_run_once_greek_no_data_writes_zero_greeks_and_logs(store, monkeypatch):
    w = _worker(store, monkeypatch, _FakeSDK(greek_status="NO_DATA"))
    r = w.run_once("POLL_ONCE", do_candles=False)
    assert r["quotes"] >= 11 and r["greeks"] == 0
    runs = store.runs(1)
    errs = runs[0]["errors_json"]
    assert "NO_DATA" in errs and "AB9019" in errs


# ------------------------------------------------------------------ look-ahead-safe retrieval
def test_get_candles_as_of_excludes_future_bars(store, monkeypatch):
    w = _worker(store, monkeypatch, _FakeSDK())
    w.run_once("POLL_ONCE", do_candles=True)
    rows = store.get_candles("NATURALGAS", "5m", kind="FUTURE")
    assert rows
    mid = rows[len(rows) // 2]["bar_start"]
    capped = store.get_candles("NATURALGAS", "5m", kind="FUTURE", as_of=mid)
    assert capped and all(r["bar_start"] <= mid for r in capped)
    assert len(capped) < len(rows)


def test_get_quotes_as_of_filters_on_exchange_ts(store, monkeypatch):
    w = _worker(store, monkeypatch, _FakeSDK())
    w.run_once("POLL_ONCE", do_candles=False)
    past = "2026-09-02T08:29:59Z"                      # one second before the fixture exch_ts
    assert store.get_quotes("NATURALGAS", kind="OPTION", as_of=past) == []
    future = "2026-09-02T08:30:01Z"
    assert store.get_quotes("NATURALGAS", kind="OPTION", as_of=future)


def test_schema_init_is_idempotent(tmp_path):
    p = str(tmp_path / "x.db")
    HistStore(p)
    s2 = HistStore(p)                                  # second init on the same file
    assert s2.summary()["candles"] == 0
