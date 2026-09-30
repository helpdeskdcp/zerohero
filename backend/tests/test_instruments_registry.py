"""app.instruments.resolve() -- the friendly-name -> {exchange, symboltoken}
registry every capture/quote path resolves through."""
from app import instruments


def test_resolve_indiavix_is_nse_amxidx():
    # MCP 3.3 data-foundation gap: India VIX capture. Token verified against
    # the cached AngelOne instrument master (data/instrument_master.json):
    # {"token": "99926017", "symbol": "India VIX", "name": "INDIA VIX",
    #  "instrumenttype": "AMXIDX", "exch_seg": "NSE"} -- never guessed.
    meta = instruments.resolve("INDIAVIX")
    assert meta is not None
    assert meta["exchange"] == "NSE"
    assert meta["symboltoken"] == "99926017"


def test_resolve_indiavix_aliases():
    for alias in ("India VIX", "NIFTY VIX", "indiavix"):
        meta = instruments.resolve(alias)
        assert meta is not None and meta["symboltoken"] == "99926017"


def test_indiavix_is_not_an_mcx_symbol():
    # histcap.worker._EXCH_OF defaults to "NSE" for anything not listed --
    # INDIAVIX must never be routed down the MCX (futures-first) capture path.
    from app.histcap.worker import _EXCH_OF
    assert _EXCH_OF.get("INDIAVIX", "NSE") == "NSE"
