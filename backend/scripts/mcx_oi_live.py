#!/usr/bin/env python3
"""
mcx_oi_live.py — live Open Interest for MCX commodities via Angel One SmartAPI V2.

RESEARCH / READ-ONLY. No order is ever placed.

What it does
------------
1. Logs in with API_KEY + CLIENT_CODE + MPIN + TOTP (pyotp), with retry.
2. Calls smartApi.getMarketData(mode="FULL", exchangeTokens={"MCX": [...]}).
   FULL mode is mandatory — `opnInterest` is not returned in LTP/OHLC modes.
3. Parses the response into a pandas DataFrame: token, ltp, oi (opnInterest).
4. Classifies each contract's Price-vs-OI relationship into
   Long Buildup / Short Buildup / Long Unwinding / Short Covering.
5. Shows the % OI-change formula using a supplied previous_oi / previous_close
   (persist yesterday's snapshot to make this real).

Notes from live testing of this account
---------------------------------------
* The OI key returned by Angel One is **`opnInterest`** (not `openInterest`).
* `getMarketData` is the current V2 method — not deprecated.
* MCX FULL quotes DO carry `opnInterest` (Crude Oil, Natural Gas, etc.).
* Rate limit: keep getMarketData(FULL) to ~1 request/second; <= 50 tokens
  per exchange per call.
* Angel One moved retail login from password to **MPIN** — pass the MPIN in the
  `generateSession(clientCode, <MPIN>, totp)` password slot.

Env vars required:
    ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_MPIN, ANGEL_TOTP_SECRET
"""
from __future__ import annotations

import os
import sys
import time
from typing import Iterable

import pandas as pd
import pyotp

try:
    from SmartApi import SmartConnect            # pip install smartapi-python
except ImportError:  # some builds expose it as smartapi
    from smartapi import SmartConnect            # type: ignore


# --------------------------------------------------------------------------- config
API_KEY = os.getenv("ANGEL_API_KEY", "")
CLIENT_CODE = os.getenv("ANGEL_CLIENT_ID", "")
MPIN = os.getenv("ANGEL_MPIN", "")               # 4/6-digit MPIN (login moved off password)
TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")

# ---- MCX contract tokens ----------------------------------------------------
# Placeholders — replace with LIVE active-contract tokens from Angel One's
# Scrip Master JSON:  https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
# (filter exch_seg == "MCX", name in {"CRUDEOIL","NATURALGAS",...}, nearest expiry)
MCX_TOKENS = [
    "434817",   # CRUDEOIL <nearest-month> FUT  <-- REPLACE
    "435110",   # NATURALGAS <nearest-month> FUT <-- REPLACE
]

# Previous session close + OI (load these from your own saved snapshot).
# Keyed by token. Used only to demonstrate the % OI-change formula.
PREVIOUS_SNAPSHOT = {
    "434817": {"previous_close": 5850.0, "previous_oi": 12850},
    "435110": {"previous_close": 245.5, "previous_oi": 41230},
}

MAX_LOGIN_RETRIES = 3
GETMARKETDATA_MIN_INTERVAL_SEC = 1.1     # respect the ~1 req/s FULL-mode limit


# --------------------------------------------------------------------------- login
class AngelSession:
    """Thin wrapper that owns the SmartConnect object and re-logs in on failure."""

    def __init__(self, api_key: str, client_code: str, mpin: str, totp_secret: str):
        if not all([api_key, client_code, mpin, totp_secret]):
            raise SystemExit("Missing one of ANGEL_API_KEY / ANGEL_CLIENT_ID / "
                             "ANGEL_MPIN / ANGEL_TOTP_SECRET in the environment.")
        self._api_key = api_key
        self._client_code = client_code
        self._mpin = mpin
        self._totp = pyotp.TOTP(totp_secret)
        self.smart: SmartConnect | None = None
        self._last_call = 0.0

    def login(self) -> SmartConnect:
        last_err = None
        for attempt in range(1, MAX_LOGIN_RETRIES + 1):
            try:
                smart = SmartConnect(api_key=self._api_key)
                data = smart.generateSession(self._client_code, self._mpin, self._totp.now())
                if not data or not data.get("status"):
                    raise RuntimeError(f"login rejected: {data}")
                # touch the profile so we fail fast if the JWT is bad
                smart.getProfile(data["data"]["refreshToken"])
                self.smart = smart
                print(f"[login] ok as {self._client_code} (attempt {attempt})")
                return smart
            except Exception as exc:                       # noqa: BLE001
                last_err = exc
                print(f"[login] attempt {attempt} failed: {exc}", file=sys.stderr)
                time.sleep(2 * attempt)
        raise SystemExit(f"login failed after {MAX_LOGIN_RETRIES} attempts: {last_err}")

    def _throttle(self) -> None:
        wait = GETMARKETDATA_MIN_INTERVAL_SEC - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def market_data_full(self, exchange_tokens: dict[str, list[str]]) -> dict:
        """getMarketData(mode='FULL', ...) with retry on transient failures.
        Re-logs in once on an auth error."""
        assert self.smart is not None
        for attempt in range(1, 4):
            self._throttle()
            try:
                resp = self.smart.getMarketData(mode="FULL", exchangeTokens=exchange_tokens)
                if resp and resp.get("status"):
                    return resp
                msg = (resp or {}).get("message", "")
                if "token" in msg.lower() or "session" in msg.lower() or "unauthor" in msg.lower():
                    print("[market_data] session expired — re-logging in", file=sys.stderr)
                    self.login()
                    continue
                raise RuntimeError(f"getMarketData not ok: {resp}")
            except Exception as exc:                       # noqa: BLE001
                print(f"[market_data] attempt {attempt} failed: {exc}", file=sys.stderr)
                time.sleep(1.5 * attempt)
        raise RuntimeError("getMarketData failed after 3 attempts")


# --------------------------------------------------------------------------- analysis
def _price_oi_trend(price_up: bool, oi_up: bool) -> str:
    if price_up and oi_up:
        return "Long Buildup"
    if not price_up and oi_up:
        return "Short Buildup"
    if not price_up and not oi_up:
        return "Long Unwinding"
    return "Short Covering"           # price up, OI down


def build_dataframe(resp: dict, previous: dict) -> pd.DataFrame:
    """resp = getMarketData FULL response. previous = {token: {previous_close, previous_oi}}."""
    fetched = (resp.get("data") or {}).get("fetched") or []
    unfetched = (resp.get("data") or {}).get("unfetched") or []
    if unfetched:
        print(f"[warn] {len(unfetched)} token(s) not returned: "
              f"{[u.get('symbolToken') for u in unfetched]}", file=sys.stderr)

    rows = []
    for item in fetched:
        token = str(item.get("symbolToken") or item.get("symboltoken") or "")
        ltp = _to_float(item.get("ltp"))
        # Angel One's key is 'opnInterest'; keep a fallback just in case.
        oi = _to_float(item.get("opnInterest") if item.get("opnInterest") is not None
                       else item.get("openInterest"))
        prev = previous.get(token, {})
        prev_close = _to_float(prev.get("previous_close"))
        prev_oi = _to_float(prev.get("previous_oi"))

        pct_oi_change = None
        if oi is not None and prev_oi not in (None, 0):
            # ((Current_OI - Previous_OI) / Previous_OI) * 100
            pct_oi_change = round((oi - prev_oi) / prev_oi * 100.0, 2)

        trend = None
        if None not in (ltp, prev_close, oi, prev_oi):
            trend = _price_oi_trend(price_up=ltp > prev_close, oi_up=oi > prev_oi)

        rows.append({
            "token": token,
            "tradingsymbol": item.get("tradingSymbol") or item.get("tradingsymbol"),
            "ltp": ltp,
            "previous_close": prev_close,
            "open_interest": oi,
            "previous_oi": prev_oi,
            "pct_oi_change": pct_oi_change,
            "Market_Trend": trend if trend is not None else "UNKNOWN (need previous snapshot)",
        })
    return pd.DataFrame(rows)


def _to_float(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- main
def main() -> None:
    session = AngelSession(API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET)
    session.login()

    tokens: Iterable[str] = [t for t in MCX_TOKENS if t]
    if not tokens:
        raise SystemExit("Fill MCX_TOKENS with real active-contract tokens first.")

    try:
        resp = session.market_data_full({"MCX": list(tokens)})   # <-- "MCX" is the key
    except Exception as exc:                                       # noqa: BLE001
        raise SystemExit(f"could not fetch MCX market data: {exc}")

    df = build_dataframe(resp, PREVIOUS_SNAPSHOT)
    if df.empty:
        raise SystemExit("no data rows returned — check the tokens and that MCX is open.")

    pd.set_option("display.width", 140)
    pd.set_option("display.max_columns", 20)
    print("\n=== MCX live OI + Price/OI trend ===")
    print(df.to_string(index=False))

    print("\n% OI-change formula:  ((Current_OI - Previous_OI) / Previous_OI) * 100")
    for _, r in df.iterrows():
        if r["pct_oi_change"] is not None:
            print(f"  {r['token']:>8}: {r['open_interest']:.0f} vs {r['previous_oi']:.0f}  "
                  f"=> {r['pct_oi_change']:+.2f}%   [{r['Market_Trend']}]")


if __name__ == "__main__":
    main()
