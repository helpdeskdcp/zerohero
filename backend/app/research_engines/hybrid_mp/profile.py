"""Market-profile detection: the Pine rules reproduced EXACTLY (including their false positives) plus a corrected, metadata-based detector.

Pine (section 01) works on substrings of syminfo.ticker / prefix / type. Confirmed weaknesses (see tests):
  * str.contains(sym, "CE") / "PE" marks ordinary equities (RELIANCE, SPICEJET, PERSISTENT ...) as options -> "Indian Index/Options";
  * str.contains(sym, "GOLD"/"SILVER") marks NSE ETFs (GOLDBEES, SILVERBEES) as MCX -> wrong session;
  * profileKnown only drives the dashboard text "PROFILE NOT RECOGNIZED"; buy/sell signals are NOT gated by it, so the text can say
    "blocked" while signals still fire.
"""
from __future__ import annotations

import re

PROFILES = ("Forex/Crypto", "Indian Index/Options", "Indian Equity", "MCX")
SESSIONS = {"Indian Index/Options": (555, 930), "Indian Equity": (555, 930), "MCX": (540, 1410), "Forex/Crypto": (0, 1440)}


def detect_profile_pine(ticker: str, prefix: str, typ: str, profile_input: str = "Auto Detect", fallback: str = "Indian Equity") -> dict:
    sym, pref, t = (ticker or "").upper(), (prefix or "").upper(), (typ or "").lower()
    is_crypto = t == "crypto" or "BINANCE" in pref or "BYBIT" in pref
    is_forex = t == "forex"
    is_mcx = "MCX" in pref or any(k in sym for k in ("CRUDEOIL", "NATURALGAS", "GOLD", "SILVER"))
    is_index = any(k in sym for k in ("NIFTY", "SENSEX", "BANKEX"))
    is_option = t == "option" or "CE" in sym or "PE" in sym
    is_indian_exch = "NSE" in pref or "BSE" in pref
    auto = ("MCX" if is_mcx else "Forex/Crypto" if (is_forex or is_crypto) else "Indian Index/Options" if (is_index or is_option)
            else "Indian Equity" if is_indian_exch else fallback)
    profile = auto if profile_input == "Auto Detect" else profile_input
    known = profile_input != "Auto Detect" or is_mcx or is_forex or is_crypto or is_index or is_option or is_indian_exch
    return dict(profile=profile, known=known, flags=dict(crypto=is_crypto, forex=is_forex, mcx=is_mcx, index=is_index, option=is_option, indian_exchange=is_indian_exch))


_OPTION_TICKER = re.compile(r"^[A-Z0-9&-]+?\d{2}[A-Z]{3}\d{2,}(CE|PE)$|^[A-Z0-9&-]+?\d{5,}(CE|PE)$|^[A-Z0-9&-]+(CE|PE)\d+$")
_COMMODITY_ROOTS = ("CRUDEOILM", "CRUDEOIL", "NATURALGASM", "NATURALGAS", "GOLDM", "GOLDPETAL", "GOLDGUINEA", "GOLD", "SILVERM", "SILVERMIC", "SILVER", "COPPER", "ZINC", "ALUMINIUM", "LEAD", "NICKEL")
_INDEX_ROOTS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX")


def detect_profile_corrected(ticker: str, exchange: str, instrument_type: str, profile_input: str = "Auto Detect", fallback: str = "Indian Equity") -> dict:
    """Backward-compatible fix: exchange and instrument type decide first; ticker text is used only where it is unambiguous.
    `exchange` = exact venue code (NSE, BSE, MCX, BINANCE ...), `instrument_type` = TradingView syminfo.type (stock, index, futures, option, fund, ...)."""
    sym, exch, t = (ticker or "").upper(), (exchange or "").upper(), (instrument_type or "").lower()
    crypto = t == "crypto" or exch in ("BINANCE", "BYBIT", "COINBASE", "KRAKEN")
    forex = t == "forex" or exch in ("FX", "OANDA", "FX_IDC", "FOREXCOM")
    mcx = exch == "MCX"
    root = re.sub(r"[0-9!]+$", "", sym)
    nse_bse = exch in ("NSE", "BSE")
    index = nse_bse and (t == "index" or root in _INDEX_ROOTS)
    option = (t == "option") or (nse_bse and t not in ("stock", "fund", "dr") and bool(_OPTION_TICKER.match(sym)))
    if mcx:
        auto = "MCX"
    elif forex or crypto:
        auto = "Forex/Crypto"
    elif index or option:
        auto = "Indian Index/Options"
    elif nse_bse:
        auto = "Indian Equity"
    else:
        auto = fallback
    profile = auto if profile_input == "Auto Detect" else profile_input
    known = profile_input != "Auto Detect" or mcx or forex or crypto or index or option or nse_bse
    return dict(profile=profile, known=known, flags=dict(crypto=crypto, forex=forex, mcx=mcx, index=index, option=option, indian_exchange=nse_bse))


def session_for(profile: str) -> tuple[int, int]:
    return SESSIONS[profile]
