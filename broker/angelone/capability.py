"""
AngelOne adapter capability report — which market-data fields the adapter can
actually source, from which endpoint, and whether it is wired into the
canonical option-chain output.

`availability` is one of:
  ANGELONE            -- the broker provides it and the adapter wires it
  ANGELONE_UNWIRED    -- the broker provides it but the adapter does not surface it
  BROKER_INTERMITTENT -- the broker sometimes returns it, sometimes null
  NOT_PROVIDED        -- AngelOne does not expose this field at all
  DERIVED_ELSEWHERE   -- must be computed by a separate engine (never in this adapter)

`status` is LIVE-CONFIRMED only after an actual AngelOne response was seen in
this process; otherwise DOC (from official SmartAPI documentation) or the last
probe error.
"""
from __future__ import annotations

# Static capability matrix. `status` starts as DOC / N-A and is upgraded by a
# live probe when credentials + market are available.
FIELD_CAPABILITIES = [
    # field           availability          endpoint                      wired  source                    note
    ("ltp",           "ANGELONE",           "market/v1/quote (FULL/LTP)",  True,  "ANGELONE_QUOTE",         "lastTradedPrice"),
    ("open",          "ANGELONE",           "market/v1/quote (FULL)",      True,  "ANGELONE_QUOTE",         ""),
    ("high",          "ANGELONE",           "market/v1/quote (FULL)",      True,  "ANGELONE_QUOTE",         ""),
    ("low",           "ANGELONE",           "market/v1/quote (FULL)",      True,  "ANGELONE_QUOTE",         ""),
    ("close",         "ANGELONE",           "market/v1/quote (FULL)",      True,  "ANGELONE_QUOTE",         "prev close"),
    ("volume",        "ANGELONE",           "market/v1/quote (FULL)",      True,  "ANGELONE_QUOTE",         "tradeVolume"),
    ("oi",            "ANGELONE",           "market/v1/quote (FULL)",      True,  "ANGELONE_QUOTE",         "opnInterest"),
    ("oi_change",     "BROKER_INTERMITTENT","market/v1/quote (FULL)",      True,  "ANGELONE_QUOTE",         "changeinOpenInterest — often null in FULL quote"),
    ("bid",           "ANGELONE",           "market/v1/quote (FULL) depth",True,  "ANGELONE_QUOTE",         "depth.buy[0].price"),
    ("ask",           "ANGELONE",           "market/v1/quote (FULL) depth",True,  "ANGELONE_QUOTE",         "depth.sell[0].price"),
    ("depth",         "ANGELONE",           "market/v1/quote (FULL) depth",True,  "ANGELONE_QUOTE",         "5-level buy/sell {price,quantity,orders}"),
    ("net_change",    "ANGELONE",           "market/v1/quote (FULL)",      True,  "ANGELONE_QUOTE",         "netChange"),
    ("pct_change",    "ANGELONE",           "market/v1/quote (FULL)",      True,  "ANGELONE_QUOTE",         "percentChange"),
    ("lower_circuit", "ANGELONE",           "market/v1/quote (FULL)",      True,  "ANGELONE_QUOTE",         ""),
    ("upper_circuit", "ANGELONE",           "market/v1/quote (FULL)",      True,  "ANGELONE_QUOTE",         ""),
    ("delta",         "ANGELONE",           "marketData/v1/optionGreek",   True,  "ANGELONE_OPTION_GREEK",  "live contracts only"),
    ("gamma",         "ANGELONE",           "marketData/v1/optionGreek",   True,  "ANGELONE_OPTION_GREEK",  "live contracts only"),
    ("theta",         "ANGELONE",           "marketData/v1/optionGreek",   True,  "ANGELONE_OPTION_GREEK",  "live contracts only"),
    ("vega",          "ANGELONE",           "marketData/v1/optionGreek",   True,  "ANGELONE_OPTION_GREEK",  "live contracts only"),
    ("iv",            "ANGELONE",           "marketData/v1/optionGreek",   True,  "ANGELONE_OPTION_GREEK",  "broker % -> stored as decimal fraction; iv_pct keeps the raw %"),
    ("strike",        "ANGELONE",           "instrument master + optionGreek", True, "ANGELONE_MASTER",    ""),
    ("option_type",   "ANGELONE",           "instrument master",          True,  "ANGELONE_MASTER",        "CE/PE from symbol suffix"),
    ("expiry",        "ANGELONE",           "instrument master",          True,  "ANGELONE_MASTER",        "DDMMMYYYY"),
    ("token",         "ANGELONE",           "instrument master",          True,  "ANGELONE_MASTER",        ""),
    ("candles OHLCV", "ANGELONE",           "historical/v1/getCandleData",True,  "ANGELONE_CANDLES",       "1m..1d, broker timestamps preserved"),
    ("rho",           "NOT_PROVIDED",       "-",                          False, "-",                      "AngelOne optionGreek returns delta/gamma/theta/vega/IV only"),
    ("charm/vanna/…", "NOT_PROVIDED",       "-",                          False, "-",                      "second-order greeks not exposed"),
    ("mid/microprice","DERIVED_ELSEWHERE",  "-",                          False, "-",                      "compute from bid/ask in an engine, not the adapter"),
    ("synthetic greeks (no-IV)", "DERIVED_ELSEWHERE", "-",                False, "-",                      "Black-Scholes fallback belongs to sr_engine / a greeks engine"),
]

_HEADERS = ("FIELD", "AVAILABILITY", "ENDPOINT", "WIRED", "SOURCE", "STATUS", "NOTE")


def adapter_capability_report(client=None, *, probe: bool = False,
                              probe_symbol: str = "NIFTY", probe_expiry: str | None = None):
    """Return [{field, availability, endpoint, wired, source, status, note}].

    probe=True + an authenticated client => one live get_quote + one live
    get_option_greeks; the greek-field STATUS becomes LIVE-CONFIRMED or the
    exact broker error (never silently 'available')."""
    rows = [dict(zip(("field", "availability", "endpoint", "wired", "source", "note"),
                     (f, a, e, w, s, n)), status="DOC")
            for (f, a, e, w, s, n) in FIELD_CAPABILITIES]
    for r in rows:
        if r["availability"] == "NOT_PROVIDED":
            r["status"] = "NOT_PROVIDED"
        elif r["availability"] == "DERIVED_ELSEWHERE":
            r["status"] = "N/A (engine)"

    if not (probe and client is not None):
        return rows

    quote_status = greek_status = "PROBE_SKIPPED"
    try:
        idx = client.resolve_index(probe_symbol)
        if idx.get("status") == "OK":
            q = client.get_quote("NSE", idx.get("token"))
            quote_status = "LIVE-CONFIRMED" if q.get("status") == "OK" else q.get("status", "ERROR")
    except Exception as e:
        quote_status = f"PROBE_ERROR:{type(e).__name__}"
    try:
        exp = probe_expiry
        if not exp:
            oc = client.resolve_option_contract(probe_symbol, "AUTO", "ATM", "CE")
            exp = oc.get("expiry") if oc.get("status") == "OK" else None
        if exp:
            g = client.get_option_greeks(probe_symbol, exp)
            greek_status = ("LIVE-CONFIRMED" if g.get("status") == "OK"
                            else f'{g.get("status")}:{g.get("errorcode") or g.get("message")}')
        else:
            greek_status = "NO_EXPIRY_RESOLVED"
    except Exception as e:
        greek_status = f"PROBE_ERROR:{type(e).__name__}"

    for r in rows:
        if r["source"] == "ANGELONE_QUOTE":
            r["status"] = quote_status
        elif r["source"] == "ANGELONE_OPTION_GREEK":
            r["status"] = greek_status
    return rows


def format_capability_report(rows) -> str:
    data = [_HEADERS] + [
        (r["field"], r["availability"], r["endpoint"], "yes" if r["wired"] else "no",
         r["source"], r["status"], r["note"]) for r in rows]
    widths = [max(len(str(row[i])) for row in data) for i in range(len(_HEADERS))]
    out = []
    for j, row in enumerate(data):
        out.append("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(_HEADERS))).rstrip())
        if j == 0:
            out.append("  ".join("-" * widths[i] for i in range(len(_HEADERS))))
    return "\n".join(out)
