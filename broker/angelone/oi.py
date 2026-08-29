def oi_value(quote):
    if not isinstance(quote, dict): return None
    return quote.get("opnInterest") if quote.get("opnInterest") is not None else quote.get("openInterest")
def normalize_oi(quote):
    return {"oi": oi_value(quote), "oi_change": quote.get("changeinOpenInterest") if isinstance(quote, dict) else None,
            "volume": quote.get("tradeVolume") if isinstance(quote, dict) else None,
            "oi_status":"AVAILABLE" if oi_value(quote) is not None else "BROKER_FEED_OI_UNAVAILABLE"}
