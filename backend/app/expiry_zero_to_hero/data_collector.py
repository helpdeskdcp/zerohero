"""
ExpiryDataCollector — pull the raw expiry-day research window from AngelOne.

DATA AVAILABILITY (verified 2026-09-03 against the live AngelOne account):

  ACTUAL (broker historical, `historical/v1/getCandleData`):
    - index 1-min OHLCV               (BSE token 99919000 for SENSEX)
    - per-strike option 1-min OHLC of the LAST-TRADED PRICE + traded volume

  UNAVAILABLE historically (no endpoint returns it for a past minute):
    - open interest, change in OI, put/call OI ratio
    - bid / ask / depth / spread
    - broker Greeks / IV  (optionGreek returns AB9019 for SENSEX anyway)
    - settlement price      (only known after 15:30; captured live if the
                             collector runs on expiry day)
    Expired weekly contracts are ALSO purged from the instrument master, so
    only the current + future weekly expiries are token-resolvable. In
    practice that means the historical option-premium series is available for
    an expiry only if this collector ran on/near that day.

  DERIVED (MODEL:BS, see bs.py):
    - IV (bisection from the last premium), Delta/Gamma/Theta/Vega
    - intrinsic value, time value, minutes-to-expiry

Nothing is fabricated. A field with no source is stored NULL with
`<field>_src = "UNAVAILABLE"`.
"""
from __future__ import annotations

import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import bs

IST = timezone(timedelta(hours=5, minutes=30))
SRC_ACTUAL = "ACTUAL:ANGEL_CANDLE"
SRC_DERIVED = "DERIVED:BS"
SRC_UNAVAIL = "UNAVAILABLE"

# BSE / SENSEX weekly options expire at 15:30 IST (index close). Kept configurable
# so the same engine can later run NIFTY/BANKNIFTY (NSE 15:30) unchanged.
EXPIRY_CLOSE_HHMM = os.environ.get("Z2H_EXPIRY_CLOSE", "15:30")

_INDEX_TOKEN = {"SENSEX": ("BSE", "99919000"), "BANKEX": ("BSE", "99919012")}
_OPT_EXCH = {"SENSEX": "BFO", "BANKEX": "BFO"}


def _mins_to_expiry(ts_ist: datetime, expiry_date: str) -> float:
    hh, mm = (int(x) for x in EXPIRY_CLOSE_HHMM.split(":"))
    d = datetime.strptime(expiry_date, "%d%b%Y").date()
    close = datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST)
    return round((close - ts_ist).total_seconds() / 60.0, 2)


class ExpiryDataCollector:
    def __init__(self, sdk):
        self.sdk = sdk

    # ------------------------------------------------------------------ strikes
    def resolve_strikes(self, index: str, expiry: str, ref_spot: float, n_each_side: int = 3):
        """ATM +/- n strikes for one index+expiry. Uses the BFO/NFO instrument
        master (modal strike gap = the grid). Returns {atm, step, strikes:[...]}
        where each entry has ce_token/pe_token or None (UNAVAILABLE)."""
        index = index.upper()
        oe = _OPT_EXCH.get(index, "NFO")
        rows = [r for r in self.sdk.search_instruments(symbol=index, exchange=oe)
                if r.get("expiry") == expiry and r.get("strike")]
        if not rows:
            return {"atm": None, "step": None, "strikes": [], "source": SRC_UNAVAIL,
                    "reason": f"no {oe} instruments for {index} {expiry} (expired contracts are purged)"}
        strikes = sorted({round(float(r["strike"]) / 100.0, 2) for r in rows})
        gaps = Counter(round(b - a, 2) for a, b in zip(strikes, strikes[1:]) if b > a)
        step = gaps.most_common(1)[0][0] if gaps else 100.0
        atm = min(strikes, key=lambda k: abs(k - float(ref_spot)))
        by = {}
        for r in rows:
            k = round(float(r["strike"]) / 100.0, 2)
            side = "CE" if str(r.get("symbol", "")).upper().endswith("CE") else "PE"
            by.setdefault(k, {})[side] = str(r.get("token"))
        out = []
        for i in range(-n_each_side, n_each_side + 1):
            k = round(atm + i * step, 2)
            legs = by.get(k, {})
            out.append({"strike": k, "offset_steps": i,
                        "moneyness": ("ATM" if i == 0 else f"{abs(i)}_" + ("ITM_PE_OTM_CE" if i > 0 else "OTM_PE_ITM_CE")),
                        "ce_token": legs.get("CE"), "pe_token": legs.get("PE")})
        return {"atm": atm, "step": step, "strikes": out, "source": SRC_ACTUAL}

    # ------------------------------------------------------------------ candles
    def _candles_1m(self, exch, token, frm, to, retries=3):
        for _ in range(retries):
            d = self.sdk.get_candles(exch, token, "ONE_MINUTE", frm, to)
            c = d.get("candles") or []
            if c:
                return c
            time.sleep(2.0)
        return []

    def collect_window(self, index: str, expiry: str, session_date: str,
                       start_hhmm: str = "14:50", end_hhmm: str = "15:40",
                       n_each_side: int = 3, ref_spot: float | None = None):
        """Pull the [start,end] IST window for `session_date` and normalise to a
        1-minute grid. Returns {meta, index_bars:[...], option_bars:[...]}.

        index_bars row : {ts, minute, spot_o/h/l/c, volume, mins_to_expiry, src}
        option_bars row: {ts, minute, strike, side, offset_steps, moneyness,
                          ltp_o/h/l/c, volume, intrinsic, time_value,
                          iv(MODEL), delta/gamma/theta_min/vega(MODEL),
                          <field>_src}
        """
        index = index.upper()
        iex, itok = _INDEX_TOKEN.get(index, ("BSE", None))
        frm = f"{_iso(session_date)} {start_hhmm}"
        to = f"{_iso(session_date)} {end_hhmm}"

        idx_c = self._candles_1m(iex, itok, frm, to) if itok else []
        # reference spot for ATM: first real bar of the window
        if ref_spot is None:
            ref_spot = idx_c[0]["close"] if idx_c else None
        sk = self.resolve_strikes(index, expiry, ref_spot or 0, n_each_side) if ref_spot else \
            {"atm": None, "step": None, "strikes": [], "source": SRC_UNAVAIL}

        idx_by_min = {r["timestamp"][:16]: r for r in idx_c}
        index_bars = []
        for m, r in sorted(idx_by_min.items()):
            ts = datetime.fromisoformat(r["timestamp"]).astimezone(IST) if "T" in r["timestamp"] \
                else datetime.strptime(r["timestamp"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
            index_bars.append({
                "ts": r["timestamp"], "minute": m[-5:],
                "spot_o": r["open"], "spot_h": r["high"], "spot_l": r["low"], "spot_c": r["close"],
                "volume": r.get("volume"), "volume_src": SRC_ACTUAL if r.get("volume") is not None else SRC_UNAVAIL,
                "mins_to_expiry": _mins_to_expiry(ts, expiry),
                "src": SRC_ACTUAL,
            })

        option_bars = []
        for s in sk.get("strikes", []):
            for side, tok in (("CE", s["ce_token"]), ("PE", s["pe_token"])):
                if not tok:
                    continue
                oc = self._candles_1m(_OPT_EXCH.get(index, "NFO"), tok, frm, to)
                time.sleep(0.8)
                obm = {r["timestamp"][:16]: r for r in oc}
                for m, r in sorted(obm.items()):
                    spot = (idx_by_min.get(m) or {}).get("close")
                    is_call = side == "CE"
                    ts = datetime.strptime(m, "%Y-%m-%dT%H:%M").replace(tzinfo=IST) if "T" in m \
                        else datetime.strptime(m, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
                    mte = _mins_to_expiry(ts, expiry)
                    ltp = r["close"]
                    intrinsic = None
                    if spot is not None:
                        intrinsic = round(max(0.0, (s["strike"] - spot) if not is_call else (spot - s["strike"])), 2)
                    tv = round(ltp - intrinsic, 2) if (ltp is not None and intrinsic is not None) else None
                    iv = bs.implied_vol(spot, s["strike"], mte, ltp, is_call) if spot else None
                    g = bs.greeks(spot, s["strike"], mte, iv, is_call) if iv else \
                        {k: None for k in ("delta", "gamma", "theta_per_min", "vega_per_volpt")}
                    option_bars.append({
                        "ts": r["timestamp"], "minute": m[-5:], "strike": s["strike"], "side": side,
                        "offset_steps": s["offset_steps"], "moneyness": s["moneyness"],
                        "ltp_o": r["open"], "ltp_h": r["high"], "ltp_l": r["low"], "ltp_c": ltp,
                        "volume": r.get("volume"),
                        "mins_to_expiry": mte,
                        "intrinsic": intrinsic, "time_value": tv,
                        "iv": round(iv, 4) if iv else None,
                        "delta": g["delta"], "gamma": g["gamma"],
                        "theta_per_min": g["theta_per_min"], "vega_per_volpt": g["vega_per_volpt"],
                        # provenance
                        "ltp_src": SRC_ACTUAL, "volume_src": SRC_ACTUAL if r.get("volume") is not None else SRC_UNAVAIL,
                        "intrinsic_src": SRC_DERIVED, "time_value_src": SRC_DERIVED,
                        "iv_src": SRC_DERIVED, "greeks_src": SRC_DERIVED,
                        "oi_src": SRC_UNAVAIL, "oi_change_src": SRC_UNAVAIL,
                        "bid_ask_src": SRC_UNAVAIL, "settlement_src": SRC_UNAVAIL,
                    })

        return {
            "meta": {
                "index": index, "expiry": expiry, "session_date": session_date,
                "window": [start_hhmm, end_hhmm], "atm": sk.get("atm"), "step": sk.get("step"),
                "ref_spot": ref_spot, "n_strikes": len(sk.get("strikes", [])),
                "index_bars": len(index_bars), "option_bars": len(option_bars),
                "data_notes": {
                    "index_ohlcv": SRC_ACTUAL, "option_ltp_ohlc": SRC_ACTUAL,
                    "option_volume": SRC_ACTUAL,
                    "greeks_iv": SRC_DERIVED + " (no broker greeks for SENSEX — AB9019)",
                    "oi_oichange_pcr": SRC_UNAVAIL + " (getCandleData has no OI; not reconstructible from candles)",
                    "bid_ask_spread": SRC_UNAVAIL,
                    "settlement": SRC_UNAVAIL + " unless collector ran live on expiry day",
                },
            },
            "index_bars": index_bars,
            "option_bars": option_bars,
        }


def _iso(ddmmm_or_iso: str) -> str:
    s = str(ddmmm_or_iso).strip()
    if len(s) == 10 and s[4] == "-":
        return s
    return datetime.strptime(s, "%d%b%Y").strftime("%Y-%m-%d")
