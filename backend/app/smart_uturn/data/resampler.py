"""Session-aware multi-timeframe construction from 1-minute candles.
Intraday N-minute bars are anchored at the SESSION OPEN (NSE/BSE 09:15), never at midnight: 3/5/15 divide the 375-minute session evenly; 30m, 45m and 60m leave a SHORT last bar (15:15-15:30), which closes at the session end.
A bar is `complete` only when every expected source minute is present (a short last bar expects fewer). Bars with gaps are KEPT but flagged (n_src < expected); no minute is ever invented.
D = exchange session day; W = ISO week (Mon-Fri trading days); M = calendar month; 3M = calendar quarter; 6M = calendar half-year. Their bar open time is the first source bar's open; `complete` is True only
when a later period exists (the last period of a file may still be running)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

IST = "Asia/Kolkata"


@dataclass(frozen=True)
class Session:
    name: str
    open_min: int          # minutes after midnight, local
    close_min: int         # exclusive: the first minute after the last 1m bar
    weekdays_only: bool = True

    @property
    def n_minutes(self) -> int:
        return self.close_min - self.open_min


NSE = Session("NSE/BSE equity+index", 9 * 60 + 15, 15 * 60 + 30)
MCX_1130 = Session("MCX (US standard time)", 9 * 60, 23 * 60 + 30)
MCX_1155 = Session("MCX (US summer time)", 9 * 60, 23 * 60 + 55)
_MIN_TFS = {"1", "3", "5", "15", "30", "45", "60", "120", "240"}
_CAL = {"D", "W", "M", "3M", "6M"}


def is_valid_tf(tf: str) -> bool:
    return tf in _MIN_TFS or tf in _CAL


def tf_minutes(tf: str) -> int | None:
    return int(tf) if tf in _MIN_TFS else None


def resample(one_min: pd.DataFrame, tf: str, session: Session = NSE) -> pd.DataFrame:
    """one_min: validated 1-minute frame (tz-aware open-time index, sorted, unique). Returns bars indexed by bar OPEN time with n_src/expected/complete/close_time."""
    if not is_valid_tf(tf): raise ValueError(f"unsupported timeframe {tf!r}")
    if len(one_min) == 0: return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "n_src", "expected", "complete", "close_time"])
    d = one_min
    if not (d.index.is_monotonic_increasing and d.index.is_unique): raise ValueError("input must be sorted and unique: run validate_one_minute first")
    loc = d.index.tz_convert(IST); mod = np.asarray(loc.hour * 60 + loc.minute); day = np.asarray(loc.normalize().tz_localize(None).values.astype("datetime64[D]"))
    if tf in _MIN_TFS:
        n = int(tf); b = (mod - session.open_min) // n
        keys = [day, b]; opens = pd.DatetimeIndex(day.astype("datetime64[ns]")) + pd.to_timedelta(session.open_min + b * n, unit="m")
    else:
        dd = pd.DatetimeIndex(day.astype("datetime64[ns]")); iso = dd.isocalendar()
        b = {"D": np.zeros(len(d), int), "W": (iso.year.to_numpy() * 100 + iso.week.to_numpy()), "M": dd.year.to_numpy() * 100 + dd.month.to_numpy(),
             "3M": dd.year.to_numpy() * 10 + (dd.month.to_numpy() - 1) // 3, "6M": dd.year.to_numpy() * 10 + (dd.month.to_numpy() - 1) // 6}[tf]
        keys = [day, b] if tf == "D" else [b]
    g = d.groupby(keys, sort=True)
    out = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last(), "n_src": g.size()})
    out["volume"] = g.volume.sum(min_count=1) if "volume" in d.columns else np.nan
    first_open = g.apply(lambda x: x.index[0], include_groups=False) if tf not in _MIN_TFS else None
    last_open = pd.Series(d.index, index=d.index).groupby(keys, sort=True).last()
    if tf in _MIN_TFS:
        idx = pd.DatetimeIndex(out.index.get_level_values(0).to_numpy().astype("datetime64[ns]")) + pd.to_timedelta(session.open_min + out.index.get_level_values(1).to_numpy() * int(tf), unit="m")
        out.index = idx.tz_localize(IST); n = int(tf)
        bidx = ((idx - idx.normalize()).total_seconds() // 60 - session.open_min) // n
        out["expected"] = np.minimum(n, session.n_minutes - bidx * n).astype(int); out["complete"] = out.n_src.to_numpy() == out.expected.to_numpy()
        out["close_time"] = out.index + pd.to_timedelta(out.expected, unit="m")
    else:
        out.index = pd.DatetimeIndex(first_open.to_numpy()).tz_convert(IST) if hasattr(first_open, "to_numpy") else first_open.index
        out["expected"] = np.nan
        out["complete"] = np.append(np.ones(len(out) - 1, bool), False) if tf != "D" else (pd.DatetimeIndex(last_open.to_numpy()).tz_convert(IST).hour * 60 + pd.DatetimeIndex(last_open.to_numpy()).tz_convert(IST).minute == session.close_min - 1)
        out["close_time"] = pd.DatetimeIndex(last_open.to_numpy()).tz_convert(IST) + pd.Timedelta(minutes=1)
    out.index.name = "open_time"
    for c in ("oi", "iv", "bid", "ask", "bid_qty", "ask_qty"):          # snapshot fields: carry the LAST snapshot of the bar (open/high/low snapshots are not kept here), never summed
        if c in d.columns: out[c] = g[c].last().to_numpy()
    return out[["open", "high", "low", "close", "volume", "n_src", "expected", "complete", "close_time"] + [c for c in ("oi", "iv", "bid", "ask", "bid_qty", "ask_qty") if c in out.columns]]


class CandleStore:
    """The one place candles are built: 1-minute source -> any supported timeframe, cached. Modules must not resample on their own."""
    def __init__(self, one_min: pd.DataFrame, session: Session = NSE):
        self.session = session; self.one_min = one_min; self._c: dict[str, pd.DataFrame] = {}

    def get(self, tf: str) -> pd.DataFrame:
        if tf not in self._c: self._c[tf] = resample(self.one_min, tf, self.session)
        return self._c[tf]
