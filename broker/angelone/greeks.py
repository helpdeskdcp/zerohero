"""
Option-Greek normalisation for the AngelOne adapter.

Data-layer only: parse AngelOne's `marketData/v1/optionGreek` rows into ONE
canonical schema, index them for O(1) per-strike/per-type lookup, and merge
them onto option-chain legs WITHOUT ever overwriting a value the quote feed
already provided.

No Black-Scholes, no synthetic greeks, no prediction, no trading logic here.
A missing/unparseable broker field becomes ``None`` — never a fabricated value.
"""
from __future__ import annotations

# The canonical greek fields the rest of the app may rely on.
CANONICAL_GREEK_FIELDS = ("delta", "gamma", "theta", "vega", "iv")

# Fields on a chain leg that greek enrichment must NEVER overwrite.
PROTECTED_LEG_FIELDS = ("ltp", "oi", "oi_change", "volume", "token",
                        "expiry", "strike", "option_type", "bid", "ask", "depth")

_GREEK_SOURCE = "ANGELONE_OPTION_GREEK"


def _f(x):
    """AngelOne sends numbers as strings ("3900.000000"). -> float | None.
    An empty string, ``None``, ``"NA"``/``"-"`` etc. -> ``None`` (never 0.0)."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x) if x == x else None            # drop NaN
    s = str(x).strip()
    if not s or s.upper() in ("NA", "N/A", "-", "NULL", "NONE"):
        return None
    try:
        v = float(s)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def normalize_greek_row(raw: dict) -> dict:
    """One AngelOne optionGreek `data[]` row -> canonical schema.

    canonical:
      strike (float, rupees) | option_type ("CE"/"PE") |
      delta,gamma,theta,vega (float|None) |
      iv (float|None, DECIMAL fraction — broker % / 100) |
      iv_pct (float|None, the raw broker percentage, unmodified) |
      trade_volume (float|None, from the greek endpoint — informational only,
        NOT merged onto the leg's `volume`) |
      source (str) | status ("OK" | "MALFORMED")
    """
    blank = {"strike": None, "option_type": None,
             "delta": None, "gamma": None, "theta": None, "vega": None,
             "iv": None, "iv_pct": None, "trade_volume": None,
             "source": _GREEK_SOURCE, "status": "MALFORMED"}
    if not isinstance(raw, dict):
        return blank

    strike = _f(raw.get("strikePrice") if raw.get("strikePrice") is not None else raw.get("strike"))
    ot = str(raw.get("optionType") or raw.get("option_type") or "").strip().upper()
    if ot not in ("CE", "PE"):
        ot = None

    iv_pct = _f(raw.get("impliedVolatility") if raw.get("impliedVolatility") is not None
                else raw.get("iv"))
    # Unit normalisation only (broker gives a percentage): iv = iv_pct / 100.
    # This is a documented representation change, not a replacement calculation.
    iv = (iv_pct / 100.0) if iv_pct is not None else None

    row = {
        "strike": strike,
        "option_type": ot,
        "delta": _f(raw.get("delta")),
        "gamma": _f(raw.get("gamma")),
        "theta": _f(raw.get("theta")),
        "vega": _f(raw.get("vega")),
        "iv": iv,
        "iv_pct": iv_pct,
        "trade_volume": _f(raw.get("tradeVolume")),
        "source": _GREEK_SOURCE,
    }
    usable = row["strike"] is not None and row["option_type"] is not None and any(
        row[k] is not None for k in CANONICAL_GREEK_FIELDS)
    row["status"] = "OK" if usable else "MALFORMED"
    return row


def index_greek_rows(rows, *, strike_round: int = 4) -> dict:
    """[normalized rows] -> {(round(strike,4), "CE"|"PE"): row}. Drops MALFORMED
    rows and anything without a strike + option_type. Later rows win on a
    duplicate key (broker should not send dupes; deterministic if it does)."""
    out = {}
    for r in rows or []:
        nr = r if r.get("source") == _GREEK_SOURCE and "status" in r else normalize_greek_row(r)
        if nr["status"] != "OK" or nr["strike"] is None or nr["option_type"] is None:
            continue
        out[(round(nr["strike"], strike_round), nr["option_type"])] = nr
    return out


def match_greek(greek_index: dict, strike, option_type, *, atol: float = 0.01,
                rtol: float = 5e-4) -> dict | None:
    """Look up a greek row for (strike, CE/PE). Exact key first, then the
    nearest strike within max(atol, rtol*strike) — instrument-master strikes
    and greek strikes can differ by sub-rupee rounding."""
    if strike is None or option_type is None:
        return None
    ot = str(option_type).upper()
    k = (round(float(strike), 4), ot)
    if k in greek_index:
        return greek_index[k]
    tol = max(atol, rtol * abs(float(strike)))
    best, best_d = None, None
    for (gs, got), row in greek_index.items():
        if got != ot:
            continue
        d = abs(gs - float(strike))
        if d <= tol and (best_d is None or d < best_d):
            best, best_d = row, d
    return best


def merge_leg_greeks(leg: dict, greek_row: dict | None) -> dict:
    """Return a NEW leg dict = leg + greek fields, and a `data_source` map.

    Rule (step 5): a greek value is written ONLY where the leg currently has
    ``None`` (or the key is absent) for that exact field. A value the quote
    feed already supplied is never overwritten. `data_source[field]` records
    the origin of whatever value ends up there.
    """
    leg = dict(leg or {})
    ds = dict(leg.get("data_source") or {})

    # record the origin of the quote-sourced fields already on the leg
    for f in ("ltp", "oi", "oi_change", "volume", "bid", "ask"):
        if leg.get(f) is not None and f not in ds:
            ds[f] = "ANGELONE_QUOTE"
    if leg.get("depth") and "depth" not in ds:
        ds["depth"] = "ANGELONE_QUOTE"

    for f in CANONICAL_GREEK_FIELDS:
        have = leg.get(f)
        if have is not None:
            ds.setdefault(f, ds.get(f) or "ANGELONE_QUOTE")   # broker quote already had it
            continue
        gv = (greek_row or {}).get(f)
        if gv is not None:
            leg[f] = gv
            ds[f] = "ANGELONE_OPTION_GREEK"
        else:
            leg.setdefault(f, None)
            ds.setdefault(f, None)

    if greek_row is not None:
        leg["iv_pct"] = greek_row.get("iv_pct") if leg.get("iv_pct") is None else leg["iv_pct"]
        leg["greeks_source"] = "BROKER"
    else:
        leg.setdefault("iv_pct", None)
        leg.setdefault("greeks_source", "UNAVAILABLE")

    leg["data_source"] = ds
    return leg


# --------------------------------------------------------------- back-compat
def get_greeks(client, symbol, expiry):
    """Deprecated shim — prefer ``client.get_option_greeks``."""
    fn = getattr(client, "get_option_greeks", None) or getattr(client, "get_greeks", None)
    return fn(symbol, expiry) if fn else {"status": "UNSUPPORTED", "rows": []}


def normalize_greeks(row):
    """Deprecated alias for ``normalize_greek_row`` (kept for old imports)."""
    r = normalize_greek_row(row if isinstance(row, dict) else {})
    return {"iv": r["iv"], "delta": r["delta"], "gamma": r["gamma"],
            "theta": r["theta"], "vega": r["vega"],
            "greeks_source": "BROKER" if r["status"] == "OK" else "UNAVAILABLE"}
