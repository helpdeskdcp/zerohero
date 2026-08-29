#!/usr/bin/env python3
"""Read-only Angel One OI/option-chain connectivity diagnostic.

This intentionally uses only the project's existing connector functions.  The
repository currently has candle and position reads, but no option-chain/OI
read API, so unsupported paths are reported explicitly and never synthesized.
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, "/root/zerohero/backend")
try:
    from dotenv import load_dotenv
    # Prefer the deployed service environment, then the workspace environment.
    for _env in (Path("/opt/chanakya-app/backend/.env"), Path("/root/zerohero/backend/.env")):
        if _env.exists():
            load_dotenv(_env, override=False)
            break
except Exception:
    pass
from app.connectors import angelone  # noqa: E402

MAX_AGE = 900


def result(name, ok, reason=""):
    print(f"{name:<24} {'PASS' if ok else 'FAIL'}" + (f" | {reason}" if reason else ""))


def _candle_read(symbol):
    meta = __import__("app.instruments", fromlist=["resolve"]).resolve(symbol) or {}
    return angelone.fetch_candles(
        market=meta.get("market"), symbol=symbol, exchange=meta.get("exchange"),
        symboltoken=meta.get("symboltoken"), interval=None, fromdate=None,
        todate=None, timeframe="5m", instrument="OPTION")


def test_nse(symbol):
    print(f"\n--- NSE {symbol} ---")
    try:
        data = _candle_read(symbol)
        status = data.get("data_status")
        print("data_status:", status)
        print("data_timestamp:", data.get("data_timestamp"))
        print("data_age_seconds:", data.get("data_age_seconds"))
        print("rows:", len(data.get("candles") or []))
        result(symbol, False, "option-chain/OI function is not implemented in existing connector")
        return False
    except Exception as e:
        result(symbol, False, f"{type(e).__name__}: {e}")
        return False


def test_mcx(symbol):
    print(f"\n--- MCX {symbol} ---")
    try:
        # Existing read-only positions endpoint does not provide MCX quote/OI.
        data = angelone.fetch_positions()
        print("positions_connector_status:", data.get("status") if isinstance(data, dict) else "UNAVAILABLE")
        result(symbol, False, "MCX OI/quote function is not implemented in existing connector")
        return False
    except Exception as e:
        result(symbol, False, f"{type(e).__name__}: {e}")
        return False


print("=" * 60)
print("CHANAKYA AI — READ-ONLY LIVE OI CONNECTIVITY TEST")
print("UTC:", datetime.now(timezone.utc).isoformat())
results = {}
for symbol in ("NIFTY", "BANKNIFTY"):
    results[symbol] = test_nse(symbol)
for symbol in ("NATGASMINI", "CRUDEOILMINI"):
    results[symbol] = test_mcx(symbol)
print("\n" + "=" * 60)
print("FINAL SUMMARY")
for name, ok in results.items():
    print(f"{name:<20} {'PASS' if ok else 'FAIL'}")
print("NSE OI:", "AVAILABLE" if any(results[x] for x in ("NIFTY", "BANKNIFTY")) else "UNAVAILABLE")
print("MCX OI:", "AVAILABLE" if any(results[x] for x in ("NATGASMINI", "CRUDEOILMINI")) else "UNAVAILABLE")
sys.exit(0 if any(results.values()) else 1)
