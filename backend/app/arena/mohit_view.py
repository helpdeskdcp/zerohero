"""Read-only view of the INSTITUTION_TRADER ("Mohit") channel signals for the chart page: date, time, entry (range), stop and T1 / T2 / T3 for each, with the option-premium candles of the day and what the arena settlement
made of it. Days the owner excluded from learning (learning.py) are NOT shown. Candles of past days are cached under data/arena/mohit_cache/ (the capture database is trimmed after a few days; the cache keeps the charts)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from . import learning as LRN, run as R, settle as S, terminal as TM

IST = timezone(timedelta(hours=5, minutes=30))
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE = os.path.join(BASE, "data", "arena", "mohit_cache")
WHO = "INSTITUTION_TRADER"
MCX_SET = {"NATURALGAS", "CRUDEOIL", "CRUDEOILM", "GOLDM", "NATGASMINI", "GOLD", "SILVER"}


def _day(t: float) -> str: return datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d")


def _signals(excl_path: str | None = None) -> list[dict]:
    sigs = [s for s in R.human_signals(include_practice=True) if s["participant"] == WHO]
    return sorted(LRN.for_learning(sigs, excl_path), key=lambda x: x["t"])


def _row(s: dict, res: dict | None) -> dict:
    tg = (s.get("targets") or []) + [None, None, None]
    r = {"id": s["id"], "date": _day(s["t"]), "time": datetime.fromtimestamp(s["t"], IST).strftime("%H:%M:%S"), "t": s["t"], "underlying": s["underlying"], "expiry": s["expiry"], "strike": s["strike"],
         "option_type": s["option_type"], "entry_mode": s["entry_mode"], "entry": s["entry"], "entry_lo": s.get("entry_lo"), "sl": s.get("sl"), "t1": tg[0], "t2": tg[1], "t3": tg[2], "lots": s.get("lots") or 1}
    if res: r["result"] = {k: res.get(k) for k in ("status", "fill", "fill_t", "exit", "exit_reason", "net_rs", "hold_sec")}
    return r


def list_signals(excl_path: str | None = None, results: dict | None = None) -> list[dict]:
    sigs = _signals(excl_path)
    if results is None:
        results = {}
        try:
            for part in R.run(sigs, market_db=os.path.join(BASE, "data", "market_history.db")).values():
                if isinstance(part, dict):
                    for x in part.get("results", []): results[x["id"]] = x
        except Exception:
            results = {}
    return [_row(s, results.get(s["id"])) for s in sigs]


def _cache_path(sid: str, cache_dir: str | None = None) -> str: return os.path.join(cache_dir or CACHE, f"{sid}.json")


def chart_for(sid: str, tf: int = 1, cache_dir: str | None = None, excl_path: str | None = None, fresh: bool = False) -> dict | None:
    """Candles (IST-shifted seconds, like the Terminal) of the signal's contract for its day + the levels. None if the id is not one of the shown signals."""
    sig = next((s for s in _signals(excl_path) if s["id"] == sid), None)
    if sig is None: return None
    cp = _cache_path(sid, cache_dir)
    if not fresh and os.path.exists(cp):
        try:
            with open(cp, encoding="utf-8") as f: return json.load(f)
        except Exception:
            pass
    day_end = datetime.fromisoformat(_day(sig["t"]) + "T23:59:00+05:30").timestamp()
    pm = TM.premium(sig["underlying"], sig["expiry"], float(sig["strike"]), sig["option_type"], tf, None, day_end)
    row = _row(sig, None); row["post_shift"] = sig["t"] + TM.IST_S
    lo, hi = ((9 * 3600, 23 * 3600 + 59 * 60) if sig["underlying"] in MCX_SET else (9 * 3600 + 15 * 60, 15 * 3600 + 30 * 60))             # the trading session only (the capture also stores quiet after-hours rows)
    candles = [c for c in pm.get("candles", []) if lo <= c["time"] % 86400 <= hi]
    out = {"signal": row, "tf": tf, "candles": candles, "cached_at": datetime.now(IST).isoformat(timespec="seconds")}
    if _day(sig["t"]) < datetime.now(IST).strftime("%Y-%m-%d") and out["candles"]:                    # a finished day never changes: keep it
        os.makedirs(cache_dir or CACHE, exist_ok=True)
        with open(cp, "w", encoding="utf-8") as f: json.dump(out, f)
    return out
