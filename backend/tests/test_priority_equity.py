"""Priority capture for NSE cash equity: watch entry without expiry / strike, token from the instrument master, an EQUITY quote row, mixed batch with an option, resilience."""
import json
import time

from app.histcap import equity_master as EM, priority as P
from tests.test_priority_capture import FakeSdk, FakeStore, meta


_MORNING = time.mktime(time.strptime("2026-10-27 10:00", "%Y-%m-%d %H:%M"))             # fixed: a watch entry lives until the 15:35 close, so the tests must not depend on the wall clock


class EqSdk(FakeSdk):
    def search_instruments(self, symbol=None, exchange=None):
        return [{"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE", "exch_seg": "NSE", "instrumenttype": ""}] if symbol == "RELIANCE" and exchange == "NSE" else []


def test_equity_watch_entry_is_registered_once_and_expires_at_the_nse_close(tmp_path):
    w = str(tmp_path / "w.json"); t = time.mktime(time.strptime("2026-10-27 10:00", "%Y-%m-%d %H:%M"))
    e = P.request_watch_equity("reliance", now=t, path=w); assert e["symbol"] == "RELIANCE" and e["expiry"] == "" and e["option_type"] == "EQ" and e["inst"] == "EQUITY"
    P.request_watch_equity("RELIANCE", now=t + 5, path=w); assert len(json.load(open(w))) == 1
    close = time.mktime(time.strptime("2026-10-27 15:35", "%Y-%m-%d %H:%M")); assert abs(e["until"] - close) < 1
    P.request_watch_equity("INFY", now=t, path=w, ttl_sec=60); assert len(json.load(open(w))) == 2


def test_step_writes_an_equity_row_with_bid_ask_and_no_contract_fields(tmp_path):
    EM._tok_cache.clear()
    w = str(tmp_path / "w.json"); now = _MORNING; P.request_watch_equity("RELIANCE", now=now, path=w)
    st, sdk = FakeStore(), EqSdk(); cap = P.PriorityCapture(store=st, sdk_provider=lambda: sdk, interval=3, path=w)
    assert cap.step(now) == 1 and sdk.calls[0][0] == {"NSE": ["2885"]} and sdk.calls[0][1] == "FULL"
    r = st.rows[0][1]; assert r["kind"] == "EQUITY" and r["symbol"] == "RELIANCE" and r["exchange"] == "NSE" and r["token"] == "2885" and r["expiry"] is None and r["strike"] is None
    assert r["bid"] == 99.5 and r["ask"] == 100.5 and r["ltp"] is not None


def test_equity_and_option_share_one_batched_request(tmp_path):
    EM._tok_cache.clear()
    w = str(tmp_path / "w.json"); now = _MORNING
    json.dump([{"symbol": "SENSEX", "expiry": "22OCT2026", "strike": 73800.0, "option_type": "PE", "until": now + 600, "kind": "signal", "added": now}], open(w, "w")); P.request_watch_equity("RELIANCE", now=now, path=w)
    sdk = EqSdk(); st = FakeStore()
    def mf(e):                                                                                    # the production resolver: contracts from the captured chain, equities from the master
        c = P.PriorityCapture(store=st, sdk_provider=lambda: sdk, path=w)
        return c._meta_equity(e) if e.get("inst") == "EQUITY" else meta(e)
    cap = P.PriorityCapture(store=st, sdk_provider=lambda: sdk, meta_fn=mf, path=w); assert cap.step(now) == 2 and len(sdk.calls) == 1
    assert sdk.calls[0][0] == {"BFO": ["73800"], "NSE": ["2885"]}


def test_unknown_equity_symbol_is_skipped_not_fatal(tmp_path):
    EM._tok_cache.clear()
    w = str(tmp_path / "w.json"); P.request_watch_equity("NOSUCH", now=_MORNING, path=w)
    st, sdk = FakeStore(), EqSdk(); assert P.PriorityCapture(store=st, sdk_provider=lambda: sdk, path=w).step(_MORNING) == 0 and sdk.calls == []
