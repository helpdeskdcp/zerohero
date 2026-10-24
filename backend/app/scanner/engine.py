"""Evaluate the scan library on the daily bars. Pure functions (testable) + the daily run that stores hits."""
from __future__ import annotations

import logging
import time
from datetime import datetime

import pandas as pd

from . import data as D, dsl, store
from .library import SCANS

_log = logging.getLogger("scanner.engine")
MIN_BARS = 130                      # shortest history any library scan needs (120-day squeeze window + margin)


def evaluate(df: pd.DataFrame, scans: dict | None = None) -> dict[str, pd.Series]:
    """name -> boolean Series (aligned to df). A scan that cannot be computed (short history, bad expression) is omitted with a warning."""
    scans = scans or SCANS; ns = dsl.namespace(df); out = {}
    for name, (side, expr) in scans.items():
        try: out[name] = dsl.run_expr(expr, df, ns)
        except Exception as e: _log.warning("scan %s failed: %r", name, e)
    return out


def run(group: str | None = None, now: datetime | None = None, loader=None, path: str | None = None, scans: dict | None = None) -> dict:
    """Latest completed bar of each instrument (optionally only the NSE or MCX group) -> hits table. Idempotent per (instrument, scan, signal date)."""
    loader = loader or D.load_daily; res = {"instruments": {}, "new_hits": []}
    for inst in D.INSTRUMENTS:
        if group and D.EXCH[inst] != group: continue
        try: df = loader(inst, now)
        except Exception as e:
            res["instruments"][inst] = {"error": repr(e)}; continue
        if len(df) < 30: res["instruments"][inst] = {"bars": len(df), "note": "history too short"}; continue
        sig = evaluate(df, scans); last = df.iloc[-1]; hit = [n for n, s in sig.items() if bool(s.iloc[-1])]
        res["instruments"][inst] = {"bars": int(len(df)), "last_date": str(pd.Timestamp(last["date"]).date()), "hits": hit, "scans_computed": len(sig)}
        for n in hit:
            if store.add_hit(inst, n, (scans or SCANS)[n][0], str(pd.Timestamp(last["date"]).date()), last["c"], path): res["new_hits"].append({"inst": inst, "scan": n, "date": str(pd.Timestamp(last["date"]).date())})
    with store.conn(path) as c: c.execute("INSERT INTO runs (ts, group_name, info) VALUES (?,?,?)", (time.time(), group, str({k: v.get("hits") for k, v in res["instruments"].items()})))
    return res
