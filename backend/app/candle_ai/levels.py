"""Previous Day / Week / Month High-Low-Close and Fibonacci pivots, causal.
For a bar on session date D, 'previous day' is the last COMPLETED session before D, 'previous week' the last completed calendar week (Mon-Sun) before D's week,
'previous month' the last completed month before D's month. Nothing from the bar's own day / week / month is used (a half-built period never leaks)."""
from __future__ import annotations

import numpy as np
import pandas as pd

FIB = {"R1": 0.382, "R2": 0.618, "R3": 1.0}
FAMILIES = {"PD": "prev day H/L/C", "PW": "prev week H/L/C", "PM": "prev month H/L/C", "FD": "Fib pivots (prev day)", "FW": "Fib pivots (prev week)", "FM": "Fib pivots (prev month)"}


def _tag(prefix: str, h, l, c) -> dict[str, pd.Series]:
    rng = h - l; p = (h + l + c) / 3.0
    out = {f"{prefix}H": h, f"{prefix}L": l, f"{prefix}C": c, f"F{prefix[1]}_P": p}
    for k, f in FIB.items():
        out[f"F{prefix[1]}_{k}"] = p + f * rng; out[f"F{prefix[1]}_S{k[1]}"] = p - f * rng
    return out


def session_date(t: pd.Series) -> pd.Series:
    return t.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None).dt.normalize()


def previous_period_levels(df: pd.DataFrame) -> pd.DataFrame:
    """df: columns t (tz-aware), h, l, c. Returns one column per level name (PDH, PDL, PDC, FD_P, FD_R1.. FD_S3, same for W and M), aligned to df's index."""
    d = pd.DataFrame({"date": session_date(df["t"]), "h": df["h"].values, "l": df["l"].values, "c": df["c"].values}, index=df.index)
    daily = d.groupby("date").agg(h=("h", "max"), l=("l", "min"), c=("c", "last"))
    out = {}
    for pref, key in (("PD", daily.index), ("PW", daily.index.to_period("W")), ("PM", daily.index.to_period("M"))):
        per = daily.groupby(key).agg(h=("h", "max"), l=("l", "min"), c=("c", "last")).shift(1)               # the PREVIOUS completed period
        bar_key = d["date"] if pref == "PD" else d["date"].dt.to_period("W" if pref == "PW" else "M")
        for name, ser in _tag(pref, per["h"], per["l"], per["c"]).items():
            out[name] = bar_key.map(ser).astype(float).values
    return pd.DataFrame(out, index=df.index)


def level_names(cols) -> list[str]:
    return [c for c in cols if c.startswith(("PD", "PW", "PM", "FD_", "FW_", "FM_"))]


def family_of(name: str) -> str:
    return name[:2] if name[0] == "P" else name[:2]                                                         # PDH->PD, FD_R1->FD
