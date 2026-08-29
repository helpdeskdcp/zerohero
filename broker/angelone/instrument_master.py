"""Reusable instrument-master facade backed by AngelOneClient."""
class InstrumentMaster:
    def __init__(self, client): self.client = client
    def load(self, refresh=False): return self.client.load_instrument_master(refresh=refresh)
    def search(self, query="", exchange=None, instrumenttype=None):
        q = str(query or "").upper()
        rows = self.client.search_instruments(exchange=exchange, instrumenttype=instrumenttype)
        return [r for r in rows if not q or q in str(r.get("name", "")).upper() or q in str(r.get("symbol", "")).upper()]
