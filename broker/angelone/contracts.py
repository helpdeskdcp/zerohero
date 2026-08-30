def resolve_option_contract(client, **kwargs): return client.resolve_option_contract(**kwargs)
def resolve_future_contract(client, **kwargs): return client.resolve_future_contract(**kwargs)
def resolve_equity(client, symbol): return client.resolve_equity(symbol)
def search_instruments(client, query="", **kwargs):
    from .instrument_master import InstrumentMaster
    return InstrumentMaster(client).search(query, **kwargs)
def search_index(client, query=""): return search_instruments(client, query, exchange="NSE", instrumenttype="AMXIDX")
def search_equity(client, query=""): return search_instruments(client, query, exchange="NSE")
def search_futures(client, query=""): return search_instruments(client, query, instrumenttype="FUTCOM")
def search_options(client, query=""): return search_instruments(client, query, instrumenttype="OPTIDX")
