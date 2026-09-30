"""app.histcap.dhan_capture -- Dhan chain -> canonical capture rows,
PARALLEL to AngelOne's own normalize.norm_quote/norm_greeks. Offline:
dhan_chain.fetch is monkeypatched, no network, no live app import."""
from app.histcap import dhan_capture as DC
from app.optionchain.chain import OptionChain, OptionLeg, StrikeRow


def _chain(spot=22763.9, ts="2026-09-30T10:45:19.019855+00:00"):
    return OptionChain(underlying="NIFTY", expiry="06OCT2026", ts=ts, source="dhan_chain", spot=spot, rows=[
        StrikeRow(strike=22750.0, ce=OptionLeg(ltp=166.95, bid=166.95, ask=167.35, bid_qty=390, ask_qty=780,
                                               oi=5670795, oi_change=4912440, volume=88082215,
                                               iv=0.115, delta=0.566, gamma=0.0011, theta=-14.17, vega=11.83,
                                               security_id="49081"),
                 pe=OptionLeg(ltp=148.3, bid=148.1, ask=148.5, bid_qty=200, ask_qty=300,
                              oi=3200000, oi_change=-300000, volume=50000000)),   # no greeks/security_id
    ])


def test_capture_dhan_chain_builds_quote_and_greek_rows(monkeypatch):
    monkeypatch.setattr(DC, "_dhan", type("M", (), {"fetch": staticmethod(lambda u, e: _chain())}))
    out = DC.capture_dhan_chain("NIFTY", "06OCT2026")
    assert out is not None
    assert out["spot"] == 22763.9
    assert len(out["quote_rows"]) == 2          # CE + PE
    assert len(out["greek_rows"]) == 1          # only CE has greeks in this fixture

    ce_q = next(r for r in out["quote_rows"] if r["option_type"] == "CE")
    assert ce_q["symbol"] == "NIFTY" and ce_q["kind"] == "OPTION" and ce_q["source"] == "DHAN_CHAIN"
    assert ce_q["strike"] == 22750.0 and ce_q["ltp"] == 166.95
    assert ce_q["bid"] == 166.95 and ce_q["ask"] == 167.35
    assert ce_q["oi"] == 5670795 and ce_q["oi_change"] == 4912440
    assert ce_q["token"] == "49081"              # real Dhan security_id used as token

    pe_q = next(r for r in out["quote_rows"] if r["option_type"] == "PE")
    assert pe_q["token"] == "DHAN-NIFTY-06OCT2026-22750.0-PE"   # synthetic fallback, no security_id given

    ce_g = out["greek_rows"][0]
    assert ce_g["source"] == "DHAN_OPTION_GREEK" and ce_g["delta"] == 0.566
    assert ce_g["iv_pct"] == 11.5


def test_capture_dhan_chain_returns_none_when_fetch_returns_none(monkeypatch):
    monkeypatch.setattr(DC, "_dhan", type("M", (), {"fetch": staticmethod(lambda u, e: None)}))
    assert DC.capture_dhan_chain("NIFTY", "06OCT2026") is None


def test_capture_dhan_chain_returns_none_when_fetch_raises(monkeypatch):
    def _boom(u, e):
        raise RuntimeError("network down")
    monkeypatch.setattr(DC, "_dhan", type("M", (), {"fetch": staticmethod(_boom)}))
    assert DC.capture_dhan_chain("NIFTY", "06OCT2026") is None


def test_capture_dhan_chain_returns_none_for_empty_chain(monkeypatch):
    empty = OptionChain(underlying="NIFTY", expiry="06OCT2026", ts="t", source="dhan_chain",
                        spot=22763.9, rows=[])
    monkeypatch.setattr(DC, "_dhan", type("M", (), {"fetch": staticmethod(lambda u, e: empty)}))
    assert DC.capture_dhan_chain("NIFTY", "06OCT2026") is None


def test_dhan_tokens_never_collide_with_angelone_numeric_tokens(monkeypatch):
    # AngelOne tokens are always plain digit strings (e.g. "49081"); a
    # synthetic Dhan token must never accidentally collide with one.
    monkeypatch.setattr(DC, "_dhan", type("M", (), {"fetch": staticmethod(lambda u, e: _chain())}))
    out = DC.capture_dhan_chain("NIFTY", "06OCT2026")
    pe_token = next(r for r in out["quote_rows"] if r["option_type"] == "PE")["token"]
    assert not pe_token.isdigit()
