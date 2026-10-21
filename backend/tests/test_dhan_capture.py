"""app.histcap.dhan_capture -- Dhan chain -> canonical capture rows,
PARALLEL to AngelOne's own normalize.norm_quote/norm_greeks. Offline:
dhan_chain.fetch_raw is monkeypatched (the one real network call), the
real dhan_chain.parse() runs on it -- no network, no live app import."""
import sqlite3

import pytest

from app.histcap import dhan_capture as DC, schema
from app.histcap.store import HistStore

_RAW = {
    "status": "success",
    "data": {
        "last_price": 22763.9,
        "oc": {
            "22750.000000": {
                "ce": {"last_price": 166.95, "oi": 5670795, "previous_oi": 758355,
                      "volume": 88082215, "top_bid_price": 166.95, "top_ask_price": 167.35,
                      "top_bid_quantity": 390, "top_ask_quantity": 780,
                      "implied_volatility": 11.5, "security_id": 49081,
                      "greeks": {"delta": 0.566, "gamma": 0.0011, "theta": -14.17, "vega": 11.83}},
                "pe": {"last_price": 148.3, "oi": 3200000, "previous_oi": 3500000,
                      "volume": 50000000, "top_bid_price": 148.1, "top_ask_price": 148.5,
                      "top_bid_quantity": 200, "top_ask_quantity": 300,
                      "implied_volatility": None},   # no greeks/security_id -- real, disclosed gap
            },
        },
    },
}


def test_capture_dhan_chain_builds_quote_and_greek_rows(monkeypatch):
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: _RAW)
    out = DC.capture_dhan_chain("NIFTY", "06OCT2026")
    assert out is not None
    assert out["spot"] == 22763.9
    assert out["raw"] is _RAW                   # archived verbatim, the one real response
    assert len(out["quote_rows"]) == 2          # CE + PE
    assert len(out["greek_rows"]) == 1          # only CE has greeks in this fixture

    ce_q = next(r for r in out["quote_rows"] if r["option_type"] == "CE")
    assert ce_q["symbol"] == "NIFTY" and ce_q["kind"] == "OPTION" and ce_q["source"] == "DHAN_CHAIN"
    assert ce_q["strike"] == 22750.0 and ce_q["ltp"] == 166.95
    assert ce_q["bid"] == 166.95 and ce_q["ask"] == 167.35
    assert ce_q["oi"] == 5670795 and ce_q["oi_change"] == 5670795 - 758355
    assert ce_q["token"] == "49081"              # real Dhan security_id used as token

    pe_q = next(r for r in out["quote_rows"] if r["option_type"] == "PE")
    assert pe_q["token"] == "DHAN-NIFTY-06OCT2026-22750.0-PE"   # synthetic fallback, no security_id given

    ce_g = out["greek_rows"][0]
    assert ce_g["source"] == "DHAN_OPTION_GREEK" and ce_g["delta"] == 0.566
    # raw 11.5 (%) -> _iv() normalises to decimal 0.115 -> iv_pct round-trips to 11.5
    assert ce_g["iv"] == 0.115 and ce_g["iv_pct"] == 11.5


def test_capture_dhan_chain_returns_none_when_fetch_raw_returns_none(monkeypatch):
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: None)
    assert DC.capture_dhan_chain("NIFTY", "06OCT2026") is None


def test_capture_dhan_chain_returns_none_when_fetch_raw_raises(monkeypatch):
    def _boom(u, e):
        raise RuntimeError("network down")
    monkeypatch.setattr(DC._dhan, "fetch_raw", _boom)
    assert DC.capture_dhan_chain("NIFTY", "06OCT2026") is None


def test_capture_dhan_chain_returns_none_for_empty_chain(monkeypatch):
    empty = {"status": "success", "data": {"last_price": 22763.9, "oc": {}}}
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: empty)
    assert DC.capture_dhan_chain("NIFTY", "06OCT2026") is None


def test_capture_dhan_chain_returns_none_for_malformed_response(monkeypatch):
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: {"status": "success"})   # missing "data" entirely
    assert DC.capture_dhan_chain("NIFTY", "06OCT2026") is None


def test_dhan_tokens_never_collide_with_angelone_numeric_tokens(monkeypatch):
    # AngelOne tokens are always plain digit strings (e.g. "49081"); a
    # synthetic Dhan token must never accidentally collide with one.
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: _RAW)
    out = DC.capture_dhan_chain("NIFTY", "06OCT2026")
    pe_token = next(r for r in out["quote_rows"] if r["option_type"] == "PE")["token"]
    assert not pe_token.isdigit()


def test_capture_dhan_chain_raw_archive_never_contains_credentials(monkeypatch):
    # The archived raw response is exactly Dhan's market-data body -- it
    # structurally cannot contain the request's auth headers (see
    # dhan_chain.fetch_raw()'s own docstring/test for the guarantee this
    # relies on). This asserts the property holds end-to-end through
    # capture_dhan_chain()'s own return value too.
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: _RAW)
    out = DC.capture_dhan_chain("NIFTY", "06OCT2026")
    dumped = str(out["raw"])
    for secret_marker in ("access-token", "access_token", "client-id", "client_id", "mpin", "otp", "password"):
        assert secret_marker not in dumped.lower()


@pytest.mark.parametrize("legacy", [False, True])
def test_same_second_provider_rows_survive_migration_and_dedup(tmp_path, monkeypatch, legacy):
    path = str(tmp_path / "providers.db")
    if legacy:
        # Original keys; remain v1 even after production DDL changes.
        ddl = schema.SCHEMA.replace("UNIQUE(source, exchange, token, snap_key)", "UNIQUE(token, snap_key)")
        ddl = ddl.replace("UNIQUE(source, underlying, expiry, strike, option_type, snap_key)",
                          "UNIQUE(underlying, expiry, strike, option_type, snap_key)")
        with sqlite3.connect(path) as c:
            c.executescript(ddl)
    else:
        HistStore(path)
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda u, e: _RAW)
    out = DC.capture_dhan_chain("NIFTY", "06OCT2026")
    dq, dg = out["quote_rows"][0], out["greek_rows"][0]
    aq = {**dq, "source": "ANGELONE_QUOTE_FULL", "instrument_key": "NFO:49081", "ltp": 100.0}
    ag = {**dg, "source": "ANGELONE_OPTION_GREEK", "delta": 0.25}
    # Include an archived raw reference whose payload has already been rotated.
    with sqlite3.connect(path) as c:
        for table, row in (("quote_snapshots", aq), ("option_greeks", ag)):
            row = {**row, "id": 41, "raw_id": 12345}
            c.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                      list(row.values()))
        c.execute("CREATE INDEX custom_quote_source ON quote_snapshots(source)")
    st = HistStore(path)
    with st.transaction() as c:
        assert st.write_quotes(c, [dq, dq], 1, {}) == 1
        assert st.write_greeks(c, [dg, dg], 1, {}) == 1
    HistStore(path)  # idempotent initialization
    with sqlite3.connect(path) as c:
        for table, field, val in (("quote_snapshots", "ltp", 100.0), ("option_greeks", "delta", 0.25)):
            assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 2
            assert c.execute(f"SELECT {field},raw_id FROM {table} WHERE id=41").fetchone() == (val, 12345)
        assert c.execute("SELECT name FROM sqlite_master WHERE name='custom_quote_source'").fetchone()


def test_capture_preserves_low_percentage_iv_units(monkeypatch):
    from copy import deepcopy
    raw = deepcopy(_RAW)
    raw["data"]["oc"]["22750.000000"]["ce"]["implied_volatility"] = 1.0
    monkeypatch.setattr(DC._dhan, "fetch_raw", lambda *a, **kw: raw)
    row = DC.capture_dhan_chain("NIFTY", "06OCT2026")["greek_rows"][0]
    assert row["iv"] == 0.01 and row["iv_pct"] == 1.0


def test_source_migration_rolls_back_both_tables_on_failure(tmp_path):
    ddl = schema.SCHEMA.replace("UNIQUE(source, exchange, token, snap_key)", "UNIQUE(token, snap_key)")
    ddl = ddl.replace("UNIQUE(source, underlying, expiry, strike, option_type, snap_key)",
                      "UNIQUE(underlying, expiry, strike, option_type, snap_key)")
    with sqlite3.connect(str(tmp_path / "rollback.db")) as c:
        c.executescript(ddl)
        c.execute("PRAGMA foreign_keys=ON")
        before = c.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        def deny_second_drop(action, name, *args):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DROP_TABLE and name == "option_greeks" else sqlite3.SQLITE_OK
        c.set_authorizer(deny_second_drop)
        with pytest.raises(sqlite3.DatabaseError):
            schema.init(c)
        c.set_authorizer(None)
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == before
