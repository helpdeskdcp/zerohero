"""Historical option engine: Greek reconstruction provenance, rolling-option
normalization, quality report, store. Pure/offline."""
import math
import sqlite3
import json
from datetime import date, datetime, timezone

import pytest

from app.hist_options import greeks_model as gm
from app.hist_options import quality, rolling
from app.hist_options.store import HistOptionStore


def _fd(model, S, K, T, iv, call, r=0.07, q=0.012):
    kw = dict(model=model, r=r, q=q)
    g = gm.reconstruct(S, K, T, iv, call, **kw)
    h = 1e-4 * S
    up = gm.price(S + h, K, T, iv, call, **kw); dn = gm.price(S - h, K, T, iv, call, **kw)
    mid = gm.price(S, K, T, iv, call, **kw)
    dv = 1e-4
    vup = gm.price(S, K, T, iv + dv, call, **kw); vdn = gm.price(S, K, T, iv - dv, call, **kw)
    dT = 1e-6
    tup = gm.price(S, K, T + dT, iv, call, **kw)
    return g, (up - dn) / (2 * h), (up - 2 * mid + dn) / h ** 2, (vup - vdn) / (2 * dv) * 0.01, \
        -(tup - mid) / dT / 365.0


@pytest.mark.parametrize("model", ["BS_CARRY", "BLACK76"])
@pytest.mark.parametrize("call", [True, False])
def test_reconstructed_greeks_match_finite_differences(model, call):
    g, d, gam, vega, theta = _fd(model, 24000.0, 24200.0, 0.04, 0.16, call)
    assert g["greek_source"] == "RECONSTRUCTED" and g["model"] == model
    assert g["delta"] == pytest.approx(d, rel=1e-4)
    assert g["gamma"] == pytest.approx(gam, rel=1e-3)
    assert g["vega"] == pytest.approx(vega, rel=1e-4)
    assert g["theta"] == pytest.approx(theta, rel=1e-3)


def test_call_put_delta_parity_black76():
    c = gm.reconstruct(290, 300, 0.1, 0.5, True, model="BLACK76", r=0.06)
    p = gm.reconstruct(290, 300, 0.1, 0.5, False, model="BLACK76", r=0.06)
    assert c["delta"] - p["delta"] == pytest.approx(math.exp(-0.06 * 0.1), rel=1e-9)


def test_provenance_and_inputs_are_recorded():
    g = gm.reconstruct(100, 100, 0.1, 0.2, True, model="BS_CARRY", r=0.05, q=0.01, iv_source="DIRECT")
    assert g["inputs"]["iv_source"] == "DIRECT" and g["inputs"]["r"] == 0.05 and g["calculation_timestamp"]


@pytest.mark.parametrize("bad", [dict(S=None), dict(K=0), dict(T=0), dict(T=-1), dict(iv=0), dict(iv=9)])
def test_bad_inputs_are_unavailable_never_guessed(bad):
    a = dict(S=100, K=100, T=0.1, iv=0.2); a.update(bad)
    g = gm.reconstruct(a["S"], a["K"], a["T"], a["iv"], True, model="BS_CARRY")
    assert g["greek_source"] == "UNAVAILABLE" and g["delta"] is None and g["reason"]


def test_unknown_model_unavailable():
    assert gm.reconstruct(1, 1, 1, 0.2, True, model="HESTON")["reason"] == "UNKNOWN_MODEL"


def test_direct_wrapper_and_empty():
    assert gm.direct(0.5, 0.01, -1, 2, source="DHAN")["greek_source"] == "DIRECT"
    assert gm.direct(source="DHAN")["greek_source"] == "UNAVAILABLE"


def _side(n=4):
    t0 = int(datetime(2026, 9, 1, 3, 45, tzinfo=timezone.utc).timestamp())   # 09:15 IST
    return {"timestamp": [t0 + 60 * i for i in range(n)], "open": [10.0] * n, "high": [11.0] * n,
            "low": [9.0] * n, "close": [10.5] * n, "volume": [100, 200, 300, 400][:n],
            "oi": [1000, 1100, 1050, 1200][:n], "iv": [16.0, 16.5, 0, 17.0][:n],
            "strike": [24000, 24000, 24000, 24050][:n], "spot": [24010.0] * n}


def test_normalize_derives_oi_change_only_within_same_strike_and_day():
    rows = rolling.normalize(_side(), underlying="NIFTY", option_type="CE")
    assert [r["oi_change"] for r in rows] == [None, 100, -50, None]     # strike changed on last bar
    assert all(r["oi_change_source"] == "DERIVED" for r in rows)


def test_normalize_iv_percent_to_decimal_and_zero_iv_is_unavailable():
    rows = rolling.normalize(_side(), underlying="NIFTY", option_type="CE")
    assert rows[0]["iv"] == pytest.approx(0.16) and rows[0]["iv_source"] == "DIRECT"
    assert rows[2]["iv"] is None and rows[2]["iv_source"] == "UNAVAILABLE"


def test_greeks_unavailable_without_expiry_and_reconstructed_with_it():
    r0 = rolling.normalize(_side(), underlying="NIFTY", option_type="CE")
    assert all(r["greek_source"] == "UNAVAILABLE" and r["greek_inputs"] for r in r0)
    r1 = rolling.normalize(_side(), underlying="NIFTY", option_type="CE", expiry_for=lambda d: date(2026, 9, 8))
    assert r1[0]["greek_source"] == "RECONSTRUCTED" and r1[0]["greek_inputs"]["expiry_date"] == "2026-09-08"
    assert r1[2]["greek_source"] == "UNAVAILABLE"                       # iv missing -> never guessed
    assert 0 < r1[0]["delta"] < 1 and r1[0]["bid"] is None and r1[0]["ask"] is None


def test_timestamp_is_utc_and_maps_to_session_open():
    r = rolling.normalize(_side(1), underlying="NIFTY", option_type="CE")[0]
    assert r["timestamp"].startswith("2026-09-01T03:45:00")


def test_support_status_mcx_is_unsupported_not_substituted(monkeypatch):
    assert rolling.support_status("CRUDEOIL") == "UNSUPPORTED_BY_DHAN"
    assert rolling.support_status("NIFTY") == "VERIFIED"
    assert rolling.support_status("GOLD") == "NOT_VERIFIED"
    assert rolling.fetch_side("CRUDEOIL", "CE", "2026-09-01", "2026-09-02") is None


def test_quality_report_flags_gaps_dupes_and_missing_greeks():
    side = _side()
    # A missing interval must be bounded by observations of the SAME contract.
    side["strike"] = [24000] * 4
    rows = rolling.normalize(side, underlying="NIFTY", option_type="CE")
    rows.append(dict(rows[0]))                              # duplicate
    del rows[2]                                             # gap between bar 2 and 4
    rep = quality.build_report(rows)
    assert rep["duplicate_records"] == 1 and rep["missing_greeks"] == len(rows)
    assert rep["timestamp_gaps"] and rep["date_coverage"]["days"] == 1
    assert rep["missing_fields"]["spot"] == 0 and 0 < rep["completeness_pct"] < 100
    assert quality.build_report([])["status"] == "EMPTY"


def test_store_is_append_only_idempotent_and_roundtrips_provenance(tmp_path):
    st = HistOptionStore(str(tmp_path / "h.db"))
    rows = rolling.normalize(_side(), underlying="NIFTY", option_type="CE", expiry_for=lambda d: date(2026, 9, 8))
    assert st.write(rows) == 4 and st.write(rows) == 0
    got = st.read("NIFTY", "CE")
    assert len(got) == 4 and got[0]["greek_inputs"]["expiry_date"] == "2026-09-08"
    assert got[0]["source"] == "DHAN_ROLLING" and got[0]["greek_source"] == "RECONSTRUCTED"


@pytest.mark.parametrize("legacy", [False, True])
def test_store_expiry_identity_preserves_contracts_and_legacy_rows(tmp_path, legacy):
    from app.hist_options.store import COLS
    path = str(tmp_path / "expiry.db")
    first = rolling.normalize(_side(1), underlying="NIFTY", option_type="CE",
                              expiry_for=lambda d: date(2026, 9, 8))[0]
    second = {**first, "expiry": "2026-09-15", "close": 20.5}
    if legacy:
        with sqlite3.connect(path) as c:
            # Original v1 key, independent of the implementation's current DDL.
            c.execute("CREATE TABLE hist_option_bars (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                      + ",".join(COLS) + ", UNIQUE(underlying, option_type, strike, timestamp, timeframe, source))")
            c.execute("INSERT INTO hist_option_bars (id," + ",".join(COLS) + ") VALUES (41,"
                      + ",".join("?" for _ in COLS) + ")",
                      [json.dumps(first[k]) if k == "greek_inputs" else first.get(k) for k in COLS])
            c.execute("UPDATE sqlite_sequence SET seq=80 WHERE name='hist_option_bars'")
    st = HistOptionStore(path)
    if not legacy:
        assert st.write([first]) == 1
    before = st.read("NIFTY")[0]
    assert st.write([first, second]) == 1
    unresolved = {**first, "expiry": None}
    assert st.write([unresolved, unresolved]) == 1
    reopened = HistOptionStore(path)
    got = reopened.read("NIFTY")
    assert len(got) == 3
    assert {r["expiry"] for r in got} == {None, "2026-09-08", "2026-09-15"}
    assert next(r for r in got if r["expiry"] == first["expiry"]) == before
    if legacy:
        assert before["id"] == 41
        assert min(r["id"] for r in got if r["id"] != 41) > 80
    assert reopened.write([first, second, unresolved]) == 0


def test_hist_option_migration_failure_preserves_original_table(tmp_path):
    from app.hist_options import store as module
    with sqlite3.connect(str(tmp_path / "rollback.db")) as c:
        c.execute("CREATE TABLE hist_option_bars (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                  + ",".join(module.COLS) + ", UNIQUE(underlying, option_type, strike, timestamp, timeframe, source))")
        c.execute("INSERT INTO hist_option_bars(id,underlying,close) VALUES (41,'NIFTY',100)")
        c.commit()
        original = c.execute("SELECT sql FROM sqlite_master WHERE name='hist_option_bars'").fetchone()
        def deny_index(action, name, *args):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_INDEX and name == "ix_hob_contract" else sqlite3.SQLITE_OK
        c.set_authorizer(deny_index)
        with pytest.raises(sqlite3.DatabaseError):
            with c:
                module._init_schema(c)
        c.set_authorizer(None)
        assert c.execute("SELECT sql FROM sqlite_master WHERE name='hist_option_bars'").fetchone() == original
        assert c.execute("SELECT id,underlying,close FROM hist_option_bars").fetchall() == [(41, "NIFTY", 100)]


def test_normalize_with_verified_resolver_records_expiry_provenance():
    ef = rolling.resolver_expiry_for("NIFTY")
    rows = rolling.normalize(_side(), underlying="NIFTY", option_type="CE", expiry_for=ef)
    assert rows[0]["expiry"] == "2026-09-01"          # 01-Sep-2026 is itself a listed Tuesday expiry
    gi = rows[0]["greek_inputs"]
    assert gi["expiry_status"] == "VERIFIED" and gi["expiry_source"] == "BROKER_METADATA"
    assert rows[0]["greek_source"] == "RECONSTRUCTED"


def test_normalize_with_unresolvable_expiry_keeps_greeks_unavailable():
    from app.hist_options.expiry import ExpiryResolver
    ef = rolling.resolver_expiry_for("NIFTY", resolver=ExpiryResolver(metadata={}, calendars={}))
    rows = rolling.normalize(_side(), underlying="NIFTY", option_type="CE", expiry_for=ef)
    assert all(r["expiry"] is None and r["greek_source"] == "UNAVAILABLE" and r["delta"] is None for r in rows)


def test_raw_values_are_not_overwritten_by_reconstruction():
    side = _side()
    rows = rolling.normalize(side, underlying="NIFTY", option_type="CE", expiry_for=rolling.resolver_expiry_for("NIFTY"))
    assert [r["iv"] * 100 for r in rows[:2]] == pytest.approx(side["iv"][:2])       # direct IV untouched
    assert [r["oi"] for r in rows] == side["oi"] and [r["spot"] for r in rows] == side["spot"]
    assert all(r["iv_source"] in ("DIRECT", "UNAVAILABLE") for r in rows)          # never RECONSTRUCTED here


@pytest.mark.parametrize("symbol", ["NIFTY", "SENSEX"])
@pytest.mark.parametrize("seconds_to_expiry", [1, 0, -1, -86400])
def test_normalize_never_reconstructs_at_or_after_expiry(monkeypatch, symbol, seconds_to_expiry):
    expiry = date(2026, 9, 1)
    cutoff = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc).timestamp()
    side = _side(1)
    side["timestamp"] = [int(cutoff - seconds_to_expiry)]
    calls = []
    original = gm.reconstruct
    def checked(S, K, T, *args, **kwargs):
        assert T > 0, "Expired contracts must not reach the reconstruction model"
        calls.append(T)
        return original(S, K, T, *args, **kwargs)
    monkeypatch.setattr(gm, "reconstruct", checked)
    row = rolling.normalize(side, underlying=symbol, option_type="CE", expiry_for=lambda d: expiry)[0]
    assert row["expiry"] == expiry.isoformat()
    assert row["close"] == side["close"][0] and row["oi"] == side["oi"][0]
    assert row["iv"] == side["iv"][0] / 100
    if seconds_to_expiry > 0:
        assert row["greek_source"] == "RECONSTRUCTED"
        assert calls == [pytest.approx(seconds_to_expiry / (365 * 86400))]
    else:
        assert calls == []
        assert row["greek_source"] == "UNAVAILABLE"
        assert all(row[k] is None for k in ("delta", "gamma", "theta", "vega"))
        assert row["greek_inputs"]["T"] <= 0
        assert row["greek_inputs"]["unavailable_reason"] == "NON_POSITIVE_TIME_TO_EXPIRY"
