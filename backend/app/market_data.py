"""Read-only market-data selection service.

This is deliberately an application boundary around the reusable AngelOne SDK:
the dashboard never receives credentials and never talks to SmartAPI itself.
"""
from datetime import datetime, timezone


def _number(value):
    """Return a broker number unchanged semantically, or None when absent."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote_fields(quote):
    quote = quote or {}
    def pick(*names):
        for name in names:
            if name in quote and quote[name] is not None:
                return quote[name]
        return None
    return {
        "ltp": _number(pick("ltp", "lastTradedPrice")),
        "open": _number(pick("open", "openPrice")),
        "high": _number(pick("high", "highPrice")),
        "low": _number(pick("low", "lowPrice")),
        "close": _number(pick("close", "closePrice")),
        "volume": _number(pick("tradeVolume", "volume")),
        "oi": _number(pick("opnInterest", "openInterest", "oi")),
        "oi_change": _number(pick("changeinOpenInterest", "changeInOpenInterest", "oiChange")),
        "timestamp": pick("exchangeTimestamp", "exchange_timestamp", "lastUpdateTime", "server_timestamp"),
    }


def _valid_date(sdk, row):
    value = row.get("expiry")
    return sdk._date(value) if value else None


def available_symbols(sdk, market):
    market = str(market or "NSE").upper()
    today = datetime.now(timezone.utc).date()
    names = {}
    for row in sdk.load_instrument_master() or []:
        exchange = str(row.get("exch_seg") or "").upper()
        kind = str(row.get("instrumenttype") or "").upper()
        if market == "NSE":
            valid = exchange == "NSE" and kind in ("AMXIDX", "EQ", "")
        elif market == "MCX":
            valid = exchange == "MCX" and kind == "FUTCOM" and _valid_date(sdk, row) and _valid_date(sdk, row) >= today
        else:
            valid = False
        if not valid:
            continue
        name = str(row.get("name") or row.get("symbol") or "").upper().strip()
        if not name:
            continue
        item = {"name": name, "exchange": exchange, "instrument_type": kind,
                "symbol": row.get("symbol"), "expiry": row.get("expiry")}
        # A symbol appears once even though MCX has several expiries.
        old = names.get(name)
        if old is None or (market == "MCX" and _valid_date(sdk, row) < _valid_date(sdk, old)):
            names[name] = item
    return sorted(names.values(), key=lambda item: item["name"])


def _unavailable(market, symbol, reason):
    return {"status": "DATA_UNAVAILABLE", "data_status": "DATA_UNAVAILABLE",
            "market": market, "symbol": symbol, "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat()}


def selection_snapshot(sdk, market, symbol, *, expiry="AUTO", option_type="BOTH", window=5, instrument=None):
    """Resolve only real master contracts and normalize quote data for the UI."""
    market, symbol = str(market or "NSE").upper(), str(symbol or "").upper()
    if market not in ("NSE", "MCX") or not symbol:
        return _unavailable(market, symbol, "market and symbol are required")
    if market == "MCX":
        contract = sdk.resolve_future_contract(symbol, expiry if str(expiry).upper() != "AUTO" else "AUTO")
        if contract.get("status") != "OK":
            return _unavailable(market, symbol, contract.get("reason") or "current MCX contract unavailable")
        quote = sdk.get_quote("MCX", contract["token"])
        fields = _quote_fields(quote)
        status = "OK" if quote.get("status") == "OK" else "DATA_UNAVAILABLE"
        # OPTIONS-on-futures view — opt-in via instrument="OPTION"; mirrors the
        # NSE chain path with the front-month future's LTP as the ATM anchor.
        if str(instrument or "").upper() == "OPTION":
            spot = fields.get("ltp")
            if spot is None:
                return _unavailable(market, symbol, "live MCX future price unavailable")
            return _option_snapshot(sdk, market, symbol, spot, expiry, option_type, window,
                                    underlying_contract=contract)
        # default: the front-month future itself (unchanged)
        return {"status": status, "data_status": status, "market": market, "symbol": symbol,
                "instrument": "FUTURE", "underlying": contract.get("underlying"),
                "contract": contract, "expiry": contract.get("expiry"), "quote": fields,
                "timestamp": fields["timestamp"] or datetime.now(timezone.utc).isoformat(),
                "source": "ANGELONE_SDK"}

    underlying = sdk.resolve_index(symbol)
    if underlying.get("status") != "OK":
        underlying = sdk.resolve_equity(symbol)
    if underlying.get("status") != "OK":
        return _unavailable(market, symbol, "underlying not found in instrument master")
    spot_quote = sdk.get_quote(underlying["exchange"], underlying["token"])
    spot = _quote_fields(spot_quote).get("ltp")
    is_index = underlying.get("exchange") == "NSE" and str(underlying.get("symbol") or "").upper() == symbol
    if str(instrument or "").upper() == "SPOT" or not is_index:
        return {"status": "OK" if spot_quote.get("status") == "OK" else "DATA_UNAVAILABLE",
                "data_status": "OK" if spot_quote.get("status") == "OK" else "DATA_UNAVAILABLE",
                "market": market, "symbol": symbol, "instrument": "SPOT", "underlying": symbol,
                "underlying_contract": underlying, "spot": spot, "quote": _quote_fields(spot_quote),
                "timestamp": _quote_fields(spot_quote)["timestamp"] or datetime.now(timezone.utc).isoformat(),
                "source": "ANGELONE_SDK"}
    if spot is None:
        return _unavailable(market, symbol, "live underlying spot unavailable")
    return _option_snapshot(sdk, market, symbol, spot, expiry, option_type, window,
                            underlying_contract=underlying)


def _option_snapshot(sdk, market, symbol, spot, expiry, option_type, window, *, underlying_contract):
    """Build the normalized ATM option chain around `spot`. Shared by the NSE
    index/stock path and the MCX options-on-futures path (the SDK's
    resolve_option_contract / get_option_chain are exchange-aware)."""
    ce = sdk.resolve_option_contract(symbol, expiry, "ATM", "CE", spot)
    pe = sdk.resolve_option_contract(symbol, expiry, "ATM", "PE", spot)
    if ce.get("status") != "OK" and pe.get("status") != "OK":
        return _unavailable(market, symbol, "valid option contract unavailable")
    selected = ce if ce.get("status") == "OK" else pe
    chain = sdk.get_option_chain(symbol, selected.get("expiry"), max(0, min(int(window), 20)))
    rows = chain.get("rows") or []
    normalized_chain = []
    for row in rows:
        ce_leg, pe_leg = row.get("ce") or {}, row.get("pe") or {}
        normalized_chain.append({"strike": row.get("strike"), "ce_ltp": ce_leg.get("ltp"), "pe_ltp": pe_leg.get("ltp"),
                                 "ce_oi": ce_leg.get("oi"), "pe_oi": pe_leg.get("oi"),
                                 "ce_oi_change": ce_leg.get("oi_change"), "pe_oi_change": pe_leg.get("oi_change"),
                                 "ce_volume": ce_leg.get("volume"), "pe_volume": pe_leg.get("volume"),
                                 "ce_token": ce_leg.get("token"), "pe_token": pe_leg.get("token")})
    chain_status = "OK" if chain.get("status") == "OK" else "DATA_UNAVAILABLE"
    return {"status": chain_status, "data_status": chain_status, "market": market, "symbol": symbol,
            "instrument": "OPTION", "underlying": symbol, "underlying_contract": underlying_contract,
            "spot": spot, "expiry": selected.get("expiry"), "atm": selected.get("strike"),
            "available_expiries": selected.get("available_expiries") or [],
            "option_type": str(option_type or "BOTH").upper(), "contracts": {"CE": ce, "PE": pe},
            "chain": normalized_chain, "timestamp": chain.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "source": "ANGELONE_SDK"}
