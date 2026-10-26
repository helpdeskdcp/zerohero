import time
import pytest
from app.nifty_direction import adapters, constituents as C, validation as V, engine as E

CSV = ("Company Name,Industry,Symbol,Series,ISIN Code\n" + "".join(f"Co{i},{'Fin' if i%2 else 'IT'},S{i},EQ,INE{i:03d}\n" for i in range(50))).encode()
import datetime as _dt
W = ("symbol,weight_pct,as_of,source\n" + "".join(f"S{i},2.0,{_dt.date.today().isoformat()},NSE Indices test\n" for i in range(50))).encode()


def mk(w=W): return C.build_master(CSV, w)


def test_master_complete_and_checksums():
    m = mk(); assert m["count"] == 50 and m["weights_status"] == "COMPLETE" and C.validate(m) == [] and len(m["csv_sha256"]) == 64


def test_missing_weights_blocks():
    m = C.build_master(CSV, None); assert m["weights_status"] == "MISSING" and C.validate(m)
    assert all(x["weight_pct"] is None for x in m["members"])


def test_partial_weights_never_estimated():
    part = ("symbol,weight_pct\n" + "".join(f"S{i},10.0\n" for i in range(10))).encode()
    m = C.build_master(CSV, part); assert m["weights_status"] == "INCOMPLETE" and all(x["weight_pct"] is None for x in m["members"])


def test_csv_schema_change_rejected():
    with pytest.raises(ValueError): C.parse_constituents_csv(b"a,b\n1,2\n")


def _quotes(now, up=True, stale=()):
    a = adapters.get("mock")
    for i in range(50):
        a.set_quote(f"S{i}", 101 if up else 99, 100, ts=now - (60 if f"S{i}" in stale else 1))
    return a.quotes([f"S{i}" for i in range(50)])


def test_fresh_universe_scores_up():
    now = time.time(); m = mk(); v = V.validate_universe(m, _quotes(now), now); s = E.snapshot(m, v)
    assert s["status"] == "OK" and s["contribution"]["net"] == pytest.approx(1.0) and s["breadth"]["advancers"] == 50 and {x["sector"] for x in s["sectors"]} == {"Fin", "IT"}


def test_stale_quotes_fail_closed():
    now = time.time(); m = mk(); v = V.validate_universe(m, _quotes(now, stale={f"S{i}" for i in range(20)}), now)
    assert v["tradable"] is False and set(v["invalid"].values()) == {"STALE"} and E.snapshot(m, v)["status"] == "NO_SIGNAL"


def test_no_weights_no_signal():
    now = time.time(); m = C.build_master(CSV, None); assert E.snapshot(m, V.validate_universe(m, _quotes(now), now))["status"] == "NO_SIGNAL"


def test_implausible_and_missing_quote_reasons():
    now = time.time(); a = adapters.MockAdapter(); a.set_quote("X", 200, 100, ts=now); assert V.check_quote(a.quotes(["X"])["X"], now) == "IMPLAUSIBLE_MOVE"
    assert V.check_quote(None, now) == "NO_QUOTE"


def test_adapter_has_no_order_surface():
    for n in adapters.names():
        a = adapters.get(n); assert not [x for x in dir(a) if any(k in x.lower() for k in ("order", "place", "buy", "sell"))]
