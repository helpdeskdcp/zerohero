def get_greeks(client, symbol, expiry): return client.get_greeks(symbol, expiry)
def normalize_greeks(row):
    if not isinstance(row, dict): return {"iv":None,"delta":None,"gamma":None,"theta":None,"vega":None,"greeks_source":"UNAVAILABLE"}
    return {k: row.get(k) for k in ("iv","delta","gamma","theta","vega")} | {"greeks_source": "BROKER"}
