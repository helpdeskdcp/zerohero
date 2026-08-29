from datetime import datetime, timezone
def get_quote(client, exchange, token): return client.get_quote(exchange, token)
def get_quotes(client, exchange, tokens): return {str(t): client.get_quote(exchange, t) for t in tokens}
def get_candles(client, *args, **kwargs):
    fn = getattr(client, "get_candles", None)
    return fn(*args, **kwargs) if fn else {"status":"UNSUPPORTED", "data_status":"DATA_UNAVAILABLE"}
def normalize_quote(q):
    if not isinstance(q, dict): return {"status":"DATA_UNAVAILABLE", "data_status":"DATA_UNAVAILABLE", "payload":None}
    return {"status":q.get("status"), "source":"ANGELONE", "exchange":q.get("exchange"), "symbol":q.get("symbol"), "token":q.get("symboltoken"), "timestamp":q.get("exchangeTimestamp") or q.get("exchange_timestamp"), "data_status":q.get("data_status"), "payload":q}
