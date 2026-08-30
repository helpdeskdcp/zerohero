def get_greeks(client, symbol, expiry): return client.get_greeks(symbol, expiry)
def normalize_greeks(row):
    if not isinstance(row, dict): return {"iv":None,"delta":None,"gamma":None,"theta":None,"vega":None,"greeks_source":"UNAVAILABLE"}
    aliases = {"iv": ("iv", "impliedVolatility", "implied_volatility"),
               "delta": ("delta",), "gamma": ("gamma",), "theta": ("theta",), "vega": ("vega",)}
    out = {k: next((row.get(a) for a in names if row.get(a) is not None), None) for k, names in aliases.items()}
    out["greeks_source"] = "BROKER" if any(v is not None for v in out.values()) else "UNAVAILABLE"
    return out
