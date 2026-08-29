def get_option_chain(client, underlying, expiry="AUTO", atm_strike=None, window=5):
    return client.get_option_chain(underlying, expiry=expiry, window=window)
