"""Isolated, backward-compatible extension of the existing Upstox connector (app/connectors/upstox.py).

The connector owns authentication (env-loaded tokens, redaction, retries); this module adds ONLY what it lacks:
  * the current-session ("intraday") candle endpoint, and
  * a candle parser / token-free health summary for the chart.
It reuses the connector's private `_authed_get` so there is exactly one auth implementation. No token or
Authorization header is read, returned or logged here.

Verified facts about the Upstox v2 historical API (2026-10-25 live probe):
  * historical intervals accepted: 1minute, 30minute, day, week, month (3/5/15 minute and 1h are NOT served -> we aggregate)
  * max range per request: 1minute <= ~1 month (28 d used), 30minute <= 90 d (88 d used), day <= ~10 y (3600 d used)
  * intraday endpoint accepts 1minute and 30minute only
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from urllib.parse import quote

from ..connectors import upstox as _u
from . import config

NATIVE_INTERVALS = ("1minute", "30minute", "day")
WINDOW_DAYS = {"1minute": 28, "30minute": 88, "day": 3600}

_gap_lock = threading.Lock()
_last_call = 0.0


class UpstoxError(RuntimeError):
    """Upstox returned an error; `code` is Upstox's error code when present. Never contains a token."""

    def __init__(self, message: str, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code, self.status = code, status


def _pace() -> None:
    """Global spacing between Upstox calls (rate-limit courtesy)."""
    global _last_call
    with _gap_lock:
        wait = config.UPSTOX_MIN_GAP_SEC - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def parse_candles(rows) -> list[tuple]:
    """Upstox rows [iso_ts, o, h, l, c, vol, oi] (newest first) -> ascending [(open_ms, o, h, l, c, v)], malformed rows dropped."""
    out = []
    for r in rows or []:
        try:
            ms = int(datetime.fromisoformat(r[0]).timestamp() * 1000)
            o, h, lo, c = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            v = float(r[5] or 0)
        except (TypeError, ValueError, IndexError):
            continue
        if not (o > 0 and h > 0 and lo > 0 and c > 0) or h < max(o, c, lo) - 1e-9 or lo > min(o, c, h) + 1e-9:
            continue                      # corrupt OHLC: skip rather than draw it
        out.append((ms, o, h, lo, c, v))
    out.sort(key=lambda b: b[0])
    return out


def _raise_for(resp) -> dict:
    try:
        j = resp.json()
    except ValueError:
        raise UpstoxError(f"Upstox returned a non-JSON response (HTTP {resp.status_code})", status=resp.status_code) from None
    if resp.status_code == 200 and j.get("status") == "success":
        return j
    err = (j.get("errors") or [{}])[0]
    code = err.get("errorCode") or err.get("error_code")
    if resp.status_code == 401 or code in ("UDAPI100050", "UDAPI100069"):
        raise UpstoxError("Upstox token invalid or expired; renew the Upstox token in the server configuration", code, resp.status_code)
    if resp.status_code == 429:
        raise UpstoxError("Upstox rate limit hit; retry shortly", code, 429)
    raise UpstoxError(f"Upstox error {code or resp.status_code}: {err.get('message', 'request failed')}", code, resp.status_code)


def fetch_historical(instrument_key: str, interval: str, to_date: str, from_date: str) -> list[tuple]:
    if interval not in NATIVE_INTERVALS:
        raise ValueError(f"interval must be one of {NATIVE_INTERVALS}")
    _pace()
    path = f"/v2/historical-candle/{quote(instrument_key, safe='')}/{interval}/{to_date}/{from_date}"
    j = _raise_for(_u._authed_get(path))
    return parse_candles((j.get("data") or {}).get("candles"))


def fetch_intraday(instrument_key: str, interval: str) -> list[tuple]:
    """Current-session candles (completed + the forming one). interval: 1minute | 30minute."""
    if interval not in ("1minute", "30minute"):
        raise ValueError("intraday interval must be 1minute or 30minute")
    _pace()
    path = f"/v2/historical-candle/intraday/{quote(instrument_key, safe='')}/{interval}"
    j = _raise_for(_u._authed_get(path))
    return parse_candles((j.get("data") or {}).get("candles"))


def auth_summary() -> dict:
    """Token-free health: only booleans/strings the connector already exposes."""
    try:
        h = _u.auth_health()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}
    return {"ok": h.get("historical_data_api") == "OK", "credentials_configured": bool(h.get("credentials_configured")),
            "api_reachable": bool(h.get("api_reachable")), "historical_data_api": h.get("historical_data_api"), "note": h.get("note")}
