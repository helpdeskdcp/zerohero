import contextlib
import json
import time

from app.histcap import priority as P


class FakeStore:
    def __init__(self): self.rows = []; self.runs = 0
    def start_run(self, *a): self.runs += 1; return 7
    @contextlib.contextmanager
    def transaction(self): yield object()
    def write_quotes(self, conn, rows, run_id, integ): self.rows += [(run_id, r) for r in rows]; return len(rows)


class FakeSdk:
    def __init__(self): self.calls = []
    def authenticate(self): return True
    def get_quotes_batch(self, by_ex, mode="FULL"):
        self.calls.append((dict(by_ex), mode))
        return {t: {"ltp": 100.0 + i, "depth": {"buy": [{"price": 99.5, "quantity": 50}], "sell": [{"price": 100.5, "quantity": 60}]}, "exchangeTimestamp": "21-Oct-2026 13:53:11"} for i, ts in enumerate(by_ex.values()) for t in ts}


def meta(e): return {"instrument_key": f"BFO:{int(e['strike'])}", "symbol": e["symbol"], "kind": "OPTION", "exchange": "BFO", "token": str(int(e["strike"])), "expiry": e["expiry"], "strike": e["strike"], "option_type": e["option_type"]}


def test_watch_list_dedupes_expires_and_formats_expiry(tmp_path):
    w = str(tmp_path / "w.json"); t = time.mktime(time.strptime("2026-10-21 13:00", "%Y-%m-%d %H:%M"))
    e = P.request_watch("SENSEX", "2026-10-22", 73800, "pe", now=t, path=w); assert e["expiry"] == "22OCT2026" and e["option_type"] == "PE"
    P.request_watch("SENSEX", "2026-10-22", 73800, "PE", now=t + 5, path=w); P.request_watch("NATURALGAS", "2026-10-23", 290, "PE", now=t, path=w)
    items = json.load(open(w)); assert len(items) == 2
    nse = next(x for x in items if x["symbol"] == "SENSEX"); mcx = next(x for x in items if x["symbol"] == "NATURALGAS")
    assert mcx["until"] - nse["until"] == 8 * 3600


def test_step_writes_dense_rows_in_one_batched_request_and_skips_expired(tmp_path):
    w = str(tmp_path / "w.json"); now = time.time()
    json.dump([{"symbol": "SENSEX", "expiry": "22OCT2026", "strike": 73800.0, "option_type": "PE", "until": now + 600}, {"symbol": "SENSEX", "expiry": "22OCT2026", "strike": 73900.0, "option_type": "PE", "until": now + 600},
               {"symbol": "SENSEX", "expiry": "22OCT2026", "strike": 70000.0, "option_type": "CE", "until": now - 5}], open(w, "w"))
    st, sdk = FakeStore(), FakeSdk(); cap = P.PriorityCapture(store=st, sdk_provider=lambda: sdk, meta_fn=meta, interval=3, path=w)
    assert cap.step(now) == 2 and len(sdk.calls) == 1 and sdk.calls[0][0] == {"BFO": ["73800", "73900"]} and sdk.calls[0][1] == "FULL"       # ONE request for all watched contracts; the expired entry is not fetched
    r = st.rows[0][1]; assert r["bid"] == 99.5 and r["ask"] == 100.5 and r["strike"] == 73800.0 and r["kind"] == "OPTION" and st.rows[0][0] == 7
    cap.step(now + 3); assert st.runs == 1                                                                                                          # one run row per day, not per cycle
    assert P.PriorityCapture(store=st, sdk_provider=lambda: sdk, meta_fn=meta, path=str(tmp_path / "none.json")).step(now) == 0                       # empty list: no broker call


def test_unresolvable_contract_is_skipped_not_fatal(tmp_path):
    w = str(tmp_path / "w.json"); json.dump([{"symbol": "SENSEX", "expiry": "22OCT2026", "strike": 1.0, "option_type": "PE", "until": time.time() + 99}], open(w, "w"))
    st, sdk = FakeStore(), FakeSdk(); assert P.PriorityCapture(store=st, sdk_provider=lambda: sdk, meta_fn=lambda e: None, path=w).step() == 0 and sdk.calls == []


def test_view_watches_expire_signals_do_not_and_signals_are_fetched_first(tmp_path):
    w = str(tmp_path / "w.json"); t = time.mktime(time.strptime("2026-10-22 11:00", "%Y-%m-%d %H:%M"))
    v = P.request_watch("NIFTY", "2026-10-27", 23000, "CE", now=t, path=w, ttl_sec=300); assert v["kind"] == "view" and v["until"] == t + 300
    again = P.request_watch("NIFTY", "2026-10-27", 23000, "CE", now=t + 10, path=w, ttl_sec=300); assert len(json.load(open(w))) == 1 and again["until"] == t + 300       # covered: no rewrite
    P.request_watch("NIFTY", "2026-10-27", 23000, "CE", now=t + 200, path=w, ttl_sec=300); assert json.load(open(w))[0]["until"] == t + 500                           # a later view extends it
    s = P.request_watch("NIFTY", "2026-10-27", 23000, "CE", now=t + 210, path=w); assert s["kind"] == "signal" and s["until"] > t + 3600                                  # a signal upgrades it to 'until close'
    P.request_watch("NIFTY", "2026-10-27", 23000, "CE", now=t + 220, path=w, ttl_sec=60); assert json.load(open(w))[0]["kind"] == "signal"                           # a view never shortens a signal
    P.request_watch("NIFTY", "2026-10-27", 23050, "CE", now=t + 230, path=w, ttl_sec=300)
    items = [e for e in json.load(open(w))]; items.sort(key=lambda e: (e.get("kind") != "signal", -e.get("added", 0))); assert [e["strike"] for e in items] == [23000.0, 23050.0]
    assert [e for e in json.load(open(w)) if e["until"] > t + 4000 and e["kind"] == "view"] == []                                                                    # views are short-lived


def test_unchanged_quote_is_stored_as_a_light_heartbeat_every_15_s_only(tmp_path):
    w = str(tmp_path / "w.json"); now = time.time()
    json.dump([{"symbol": "SENSEX", "expiry": "22OCT2026", "strike": 73800.0, "option_type": "PE", "until": now + 600, "kind": "signal"}], open(w, "w"))
    st, sdk = FakeStore(), FakeSdk(); cap = P.PriorityCapture(store=st, sdk_provider=lambda: sdk, meta_fn=meta, interval=3, path=w)
    assert cap.step(now) == 1                                              # first sight: stored
    assert cap.step(now + 4) == 0 and cap.step(now + 10) == 0              # same exchange timestamp, less than 15 s later: nothing new to store
    assert cap.step(now + 16) == 1                                         # 15 s passed: a heartbeat row says "the quote is still standing"
    hb = st.rows[-1][1]; assert hb["snap_key"].startswith("HB|") and hb["depth_json"] is None and hb["bid"] == 99.5
    assert cap.status()["last_cycle_age_s"] is not None
