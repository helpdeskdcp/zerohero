#!/usr/bin/env python3
"""Read-only real AngelOne contract/OI/Greeks diagnostic."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
try:
    from dotenv import load_dotenv
    for p in (Path("/opt/chanakya-app/backend/.env"), Path("/root/zerohero/backend/.env")):
        if p.exists(): load_dotenv(p, override=False); break
except Exception: pass
from broker.angelone import AngelOneClient

def mark(label, ok, reason=""):
    print(f"{label}: {'PASS' if ok else 'FAIL'}" + (f" | {reason}" if reason else ""))

client = AngelOneClient(cache_path="/root/zerohero/data/instrument_master.json")
print("=" * 48); print("ANGELONE REAL CONTRACT/OI/GREEKS TEST")
auth = client.authenticate(); mark("AUTHENTICATION", auth, "credentials/session unavailable" if not auth else "")
master = client.load_instrument_master(); mark("INSTRUMENT_MASTER", bool(master), "empty or unavailable")

for symbol in ("NIFTY", "BANKNIFTY"):
    print(f"\n{symbol}")
    idx = client.resolve_index(symbol); mark("Contract", idx.get("status") == "OK", idx.get("status", ""))
    q = client.get_quote("NSE", idx.get("token")) if idx.get("status") == "OK" else {}
    spot = q.get("ltp") or q.get("lastTradedPrice")
    chain = client.get_option_chain(symbol, "AUTO", 5) if spot is not None else {}
    mark("Option chain", chain.get("status") == "OK", chain.get("reason", ""))
    rows = chain.get("rows") or []
    for typ in ("ce", "pe"):
        legs=[r.get(typ) for r in rows if r.get(typ)]
        leg=legs[len(legs)//2] if legs else {}
        for field in ("token", "ltp", "oi", "oi_change", "volume"):
            value=leg.get(field); mark(f"{typ.upper()} {field}", value is not None and (field != "oi" or value is not None), "unavailable" if value is None else "")
    g = client.get_greeks(symbol, chain.get("expiry")) if chain.get("expiry") else {}
    mark("Greeks", g.get("status") == "OK", "broker unavailable" if g.get("status") != "OK" else "")

for symbol in ("NATGASMINI", "CRUDEOILMINI"):
    print(f"\n{symbol}")
    rows=client.search_instruments(symbol=symbol, exchange="MCX", instrumenttype="FUTCOM")
    valid=[r for r in rows if r.get("token") and r.get("expiry")]
    mark("Contract", bool(valid), "INSTRUMENT_MASTER_CONTRACT_NOT_FOUND" if not valid else "")
    if valid:
        r=sorted(valid,key=lambda x:x.get("expiry"))[0]; q=client.get_quote("MCX",r["token"])
        mark("Quote", q.get("status") == "OK"); mark("OI", q.get("opnInterest") is not None, "BROKER_FEED_OI_UNAVAILABLE" if q.get("opnInterest") is None else "")
    else: mark("Quote", False, "contract unavailable"); mark("OI", False, "contract unavailable")
print("\nMCX Options: NOT_SUPPORTED (no broker chain capability confirmed)")
