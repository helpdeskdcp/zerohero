"""app.optionchain.resolve.check_data_conflict -- MCP 3.3 section 2/3's
DATA_CONFLICT check (angelone_captured vs dhan_chain spot agreement).
Offline: both sources are monkeypatched, no network, no live app import."""
from app.optionchain import resolve as R
from app.optionchain.chain import OptionChain, OptionLeg, StrikeRow
from app.optionchain.sources import dhan_chain as _dhan


def _chain(spot, source="angelone_captured"):
    return OptionChain(underlying="NIFTY", expiry="06OCT2026", ts="t", source=source, spot=spot,
                       rows=[StrikeRow(strike=22750, ce=OptionLeg(ltp=100.0))])


def test_conflict_detected_when_spots_disagree_beyond_tolerance(monkeypatch):
    monkeypatch.setattr(R, "get_chain", lambda u, e, allow_network=True: _chain(22750.0))
    monkeypatch.setattr(_dhan, "fetch", lambda u, e: _chain(23000.0, "dhan_chain"))
    out = R.check_data_conflict("NIFTY")
    assert out["status"] == "CONFLICT"
    assert out["angelone_spot"] == 22750.0 and out["dhan_spot"] == 23000.0
    assert out["diff_pct"] > out["tolerance_pct"]


def test_ok_when_spots_agree_within_tolerance(monkeypatch):
    monkeypatch.setattr(R, "get_chain", lambda u, e, allow_network=True: _chain(22750.0))
    monkeypatch.setattr(_dhan, "fetch", lambda u, e: _chain(22755.0, "dhan_chain"))
    out = R.check_data_conflict("NIFTY")
    assert out["status"] == "OK"


def test_unavailable_when_angelone_chain_missing(monkeypatch):
    monkeypatch.setattr(R, "get_chain", lambda u, e, allow_network=True: None)
    out = R.check_data_conflict("NIFTY")
    assert out["status"] == "UNAVAILABLE"


def test_unavailable_when_dhan_chain_missing(monkeypatch):
    monkeypatch.setattr(R, "get_chain", lambda u, e, allow_network=True: _chain(22750.0))
    monkeypatch.setattr(_dhan, "fetch", lambda u, e: None)
    out = R.check_data_conflict("NIFTY")
    assert out["status"] == "UNAVAILABLE"


def test_unavailable_when_dhan_fetch_raises(monkeypatch):
    monkeypatch.setattr(R, "get_chain", lambda u, e, allow_network=True: _chain(22750.0))

    def _boom(u, e):
        raise RuntimeError("boom")
    monkeypatch.setattr(_dhan, "fetch", _boom)
    out = R.check_data_conflict("NIFTY")
    assert out["status"] == "UNAVAILABLE"


def test_tolerance_is_configurable(monkeypatch):
    monkeypatch.setattr(R, "get_chain", lambda u, e, allow_network=True: _chain(22750.0))
    monkeypatch.setattr(_dhan, "fetch", lambda u, e: _chain(22800.0, "dhan_chain"))
    assert R.check_data_conflict("NIFTY")["status"] == "OK"                              # default 0.5%
    assert R.check_data_conflict("NIFTY", tolerance_pct=0.1)["status"] == "CONFLICT"      # tighter
