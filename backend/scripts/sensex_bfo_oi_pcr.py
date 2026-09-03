#!/usr/bin/env python3
"""
sensex_bfo_oi_pcr.py — live SENSEX (BSE derivatives) Open Interest + Put/Call
Ratio + sentiment, via Angel One SmartAPI V2.

RESEARCH / READ-ONLY. No order is ever placed.

CRITICAL — exchange identifier
------------------------------
For BSE SENSEX / BANKEX **options and futures** the Angel One SmartAPI exchange
key is **"BFO"** (BSE Futures & Options), NOT "BSE". "BSE" is the cash segment.
The getMarketData payload MUST use "BFO":

    exchangeTokens = {"BFO": ["<token1>", "<token2>", ...]}

What it does
------------
1. Logs in with API_KEY + CLIENT_CODE + MPIN + TOTP (pyotp), with retry.
2. getMarketData(mode="FULL", exchangeTokens={"BFO": [...]}) — FULL is required
   for `opnInterest`.
3. Parses to a pandas DataFrame: token, ltp, oi, and (CE|PE) inferred from the
   trading symbol.
4. PCR (OI) = sum(PE OI) / sum(CE OI), with a sentiment band.

Verified notes for this account
-------------------------------
* OI key is **`opnInterest`**.
* `getOptionGreek` returns AB9019 for SENSEX (no broker greeks) — irrelevant
  here, we only need OI from the FULL quote, which DOES populate for BFO.
* Rate limit: getMarketData(FULL) ~1 req/s, <= 50 tokens per exchange per call;
  chunk larger baskets.
* Login uses MPIN (retail password login is retired).

Env: ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_MPIN, ANGEL_TOTP_SECRET
"""
from __future__ import annotations

import os
import sys
import time

import pandas as pd
import pyotp

try:
    from SmartApi import SmartConnect            # pip install smartapi-python
except ImportError:
    from smartapi import SmartConnect            # type: ignore


API_KEY = os.getenv("ANGEL_API_KEY", "")
CLIENT_CODE = os.getenv("ANGEL_CLIENT_ID", "")
MPIN = os.getenv("ANGEL_MPIN", "")
TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")

# ---- SENSEX option contract tokens (BFO) -----------------------------------
# Placeholders. Fetch the exact ACTIVE tokens from Angel One's Scrip Master:
#   https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
# filter:  exch_seg == "BFO"  and  name == "SENSEX"  and  expiry == <nearest weekly>
# take a strip of strikes around ATM, both CE and PE. The symbol looks like
# "SENSEX2690376500CE" — the last two chars (CE/PE) are what we key on below.
BFO_SENSEX_TOKENS = [
    "12345",   # SENSEX <exp> <ATM-200> CE   <-- REPLACE with real tokens
    "12346",   # SENSEX <exp> <ATM-200> PE   <-- REPLACE
    "12347",   # SENSEX <exp> <ATM-100> CE   <-- REPLACE
    "12348",   # SENSEX <exp> <ATM-100> PE   <-- REPLACE
    "12349",   # SENSEX <exp> <ATM>     CE   <-- REPLACE
    "12350",   # SENSEX <exp> <ATM>     PE   <-- REPLACE
    "12351",   # SENSEX <exp> <ATM+100> CE   <-- REPLACE
    "12352",   # SENSEX <exp> <ATM+100> PE   <-- REPLACE
]

MAX_LOGIN_RETRIES = 3
GETMARKETDATA_MIN_INTERVAL_SEC = 1.1
TOKENS_PER_CALL = 50                         # Angel One hard limit per exchange


class AngelSession:
    def __init__(self, api_key, client_code, mpin, totp_secret):
        if not all([api_key, client_code, mpin, totp_secret]):
            raise SystemExit("Missing ANGEL_API_KEY / ANGEL_CLIENT_ID / ANGEL_MPIN / "
                             "ANGEL_TOTP_SECRET in the environment.")
        self._api_key, self._client_code, self._mpin = api_key, client_code, mpin
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
                smart.getProfile(data["data"]["refreshToken"])
                self.smart = smart
                print(f"[login] ok as {self._client_code} (attempt {attempt})")
                return smart
            except Exception as exc:                       # noqa: BLE001
                last_err = exc
                print(f"[login] attempt {attempt} failed: {exc}", file=sys.stderr)
                time.sleep(2 * attempt)
        raise SystemExit(f"login failed after {MAX_LOGIN_RETRIES} attempts: {last_err}")

    def _throttle(self):
        wait = GETMARKETDATA_MIN_INTERVAL_SEC - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def market_data_full(self, exchange: str, tokens: list[str]) -> list[dict]:
        """Chunked getMarketData(FULL). Returns the merged `fetched` list."""
        assert self.smart is not None
        fetched: list[dict] = []
        for i in range(0, len(tokens), TOKENS_PER_CALL):
            chunk = tokens[i:i + TOKENS_PER_CALL]
            for attempt in range(1, 4):
                self._throttle()
                try:
                    resp = self.smart.getMarketData(mode="FULL",
                                                    exchangeTokens={exchange: chunk})
                    if resp and resp.get("status"):
                        data = resp.get("data") or {}
                        fetched.extend(data.get("fetched") or [])
                        for u in (data.get("unfetched") or []):
                            print(f"[warn] unfetched token {u.get('symbolToken')}", file=sys.stderr)
                        break
                    msg = (resp or {}).get("message", "").lower()
                    if any(k in msg for k in ("token", "session", "unauthor")):
                        print("[market_data] session expired — re-login", file=sys.stderr)
                        self.login()
                        continue
                    raise RuntimeError(f"getMarketData not ok: {resp}")
                except Exception as exc:                   # noqa: BLE001
                    print(f"[market_data] chunk {i//TOKENS_PER_CALL} attempt {attempt} "
                          f"failed: {exc}", file=sys.stderr)
                    time.sleep(1.5 * attempt)
            else:
                raise RuntimeError("getMarketData failed for a chunk after 3 attempts")
        return fetched


def _to_float(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _side_from_symbol(sym: str) -> str | None:
    s = (sym or "").upper().strip()
    if s.endswith("CE"):
        return "CE"
    if s.endswith("PE"):
        return "PE"
    return None


def build_dataframe(fetched: list[dict]) -> pd.DataFrame:
    rows = []
    for item in fetched:
        sym = item.get("tradingSymbol") or item.get("tradingsymbol") or ""
        rows.append({
            "token": str(item.get("symbolToken") or item.get("symboltoken") or ""),
            "tradingsymbol": sym,
            "side": _side_from_symbol(sym),
            "ltp": _to_float(item.get("ltp")),
            "open_interest": _to_float(item.get("opnInterest") if item.get("opnInterest") is not None
                                      else item.get("openInterest")),
        })
    return pd.DataFrame(rows)


def pcr_and_sentiment(df: pd.DataFrame) -> dict:
    puts = df.loc[df["side"] == "PE", "open_interest"].dropna()
    calls = df.loc[df["side"] == "CE", "open_interest"].dropna()
    total_pe, total_ce = float(puts.sum()), float(calls.sum())
    if total_ce <= 0:
        return {"pcr": None, "sentiment": "INSUFFICIENT_DATA",
                "total_pe_oi": total_pe, "total_ce_oi": total_ce}
    pcr = total_pe / total_ce
    if pcr > 1.25:
        sentiment = "Strong Support / Bullish"
    elif pcr < 0.70:
        sentiment = "Strong Resistance / Bearish"
    else:
        sentiment = "Neutral / Rangebound"
    return {"pcr": round(pcr, 3), "sentiment": sentiment,
            "total_pe_oi": total_pe, "total_ce_oi": total_ce,
            "n_pe": int(puts.count()), "n_ce": int(calls.count())}


def main() -> None:
    session = AngelSession(API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET)
    session.login()

    tokens = [t for t in BFO_SENSEX_TOKENS if t]
    if not tokens:
        raise SystemExit("Fill BFO_SENSEX_TOKENS with real active SENSEX option tokens.")

    try:
        fetched = session.market_data_full("BFO", tokens)      # <-- "BFO", not "BSE"
    except Exception as exc:                                    # noqa: BLE001
        raise SystemExit(f"could not fetch BFO market data: {exc}")

    df = build_dataframe(fetched)
    if df.empty:
        raise SystemExit("no rows returned — check the tokens and that BFO is open.")

    pd.set_option("display.width", 140)
    print("\n=== SENSEX (BFO) live OI ===")
    print(df.sort_values(["side", "token"]).to_string(index=False))

    res = pcr_and_sentiment(df)
    print("\n=== Put-Call Ratio (OI) ===")
    print(f"  Total PE OI : {res['total_pe_oi']:,.0f}")
    print(f"  Total CE OI : {res['total_ce_oi']:,.0f}")
    print(f"  PCR (OI)    : {res['pcr']}")
    print(f"  Sentiment   : {res['sentiment']}")
    print("\n  bands:  PCR > 1.25 Bullish/Support | < 0.70 Bearish/Resistance | else Neutral")
    if any(df["open_interest"].isna()):
        print("\n[warn] some contracts returned no opnInterest — if this is ALL of them, "
              "confirm the exchange key is 'BFO' and mode is 'FULL'.", file=sys.stderr)


if __name__ == "__main__":
    main()
