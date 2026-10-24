"""Safe scan expression language. A scan is ONE expression over daily bars, e.g. `close > hh(20) & rsi(14) < 70`. It is parsed with `ast`, only a whitelist of
nodes/functions is allowed (no attributes, subscripts, lambdas, comprehensions, imports), and it is evaluated on pandas Series -- every function is causal
(bar t uses bars <= t), `ref(x, k)` shifts k bars back. Combine conditions with & | ~ (not `and` / `or`)."""
from __future__ import annotations

import ast

import numpy as np
import pandas as pd

from ..candle_ai import levels as LV

_ALLOWED_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.Call, ast.Name, ast.Load, ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow,
                  ast.USub, ast.UAdd, ast.Not, ast.Invert, ast.BitAnd, ast.BitOr, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


class ScanError(ValueError):
    pass


def validate(expr: str, names: set[str]) -> ast.Expression:
    if len(expr) > 600: raise ScanError("expression too long")
    try: tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e: raise ScanError(f"syntax error: {e.msg}")
    for n in ast.walk(tree):
        if not isinstance(n, _ALLOWED_NODES): raise ScanError(f"not allowed: {type(n).__name__}")
        if isinstance(n, ast.Call) and not (isinstance(n.func, ast.Name) and n.func.id in names): raise ScanError("only whitelisted functions may be called")
        if isinstance(n, ast.Call) and n.keywords: raise ScanError("keyword arguments are not allowed")
        if isinstance(n, ast.Constant) and not isinstance(n.value, (int, float, bool)): raise ScanError("only numeric constants")
        if isinstance(n, ast.Name) and n.id not in names: raise ScanError(f"unknown name: {n.id}")
    return tree


def _atr(h, l, c, n):
    pc = c.shift(1); tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1); return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def _rsi(x, n):
    d = x.diff(); up = d.clip(lower=0).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return out.where(~((dn == 0) & up.notna()), np.where(up > 0, 100.0, 50.0))                       # no losses in the window: RSI is 100 (50 if nothing moved), not NaN


def namespace(df: pd.DataFrame) -> dict:
    """df: columns date (datetime64), o, h, l, c, v ascending. Returns name -> Series / function (all causal)."""
    o, h, l, c, v = (df[k].astype(float).reset_index(drop=True) for k in ("o", "h", "l", "c", "v"))
    t = pd.to_datetime(df["date"]).dt.tz_localize("Asia/Kolkata") + pd.Timedelta(hours=9, minutes=15)
    lev = LV.previous_period_levels(pd.DataFrame({"t": t.dt.tz_convert("UTC").values, "h": h.values, "l": l.values, "c": c.values}).assign(t=lambda x: pd.to_datetime(x["t"], utc=True))); lev = lev.reset_index(drop=True)
    ns = {"open": o, "high": h, "low": l, "close": c, "volume": v,
          "sma": lambda x, n: x.rolling(int(n), min_periods=int(n)).mean(), "ema": lambda x, n: x.ewm(span=int(n), adjust=False, min_periods=int(n)).mean(),
          "rsi": lambda x, n: _rsi(x, int(n)), "atr": lambda n: _atr(h, l, c, int(n)), "ref": lambda x, k: x.shift(int(k)),
          "hh": lambda n: h.shift(1).rolling(int(n), min_periods=int(n)).max(), "ll": lambda n: l.shift(1).rolling(int(n), min_periods=int(n)).min(),
          "highest": lambda x, n: x.rolling(int(n), min_periods=int(n)).max(), "lowest": lambda x, n: x.rolling(int(n), min_periods=int(n)).min(),
          "stdev": lambda x, n: x.rolling(int(n), min_periods=int(n)).std(ddof=0), "abs": lambda x: x.abs(), "max": lambda a, b: pd.concat([a, b], axis=1).max(axis=1) if hasattr(a, "shape") or hasattr(b, "shape") else max(a, b),
          "min": lambda a, b: pd.concat([a, b], axis=1).min(axis=1) if hasattr(a, "shape") or hasattr(b, "shape") else min(a, b),
          "crosses_above": lambda a, b: (a > b) & (a.shift(1) <= (b.shift(1) if hasattr(b, "shift") else b)), "crosses_below": lambda a, b: (a < b) & (a.shift(1) >= (b.shift(1) if hasattr(b, "shift") else b)),
          "bbw": lambda n: (4 * c.rolling(int(n), min_periods=int(n)).std(ddof=0)) / c.rolling(int(n), min_periods=int(n)).mean(),
          "bb_upper": lambda n: c.rolling(int(n), min_periods=int(n)).mean() + 2 * c.rolling(int(n), min_periods=int(n)).std(ddof=0),
          "bb_lower": lambda n: c.rolling(int(n), min_periods=int(n)).mean() - 2 * c.rolling(int(n), min_periods=int(n)).std(ddof=0),
          "dom": pd.to_datetime(df["date"]).dt.day.reset_index(drop=True), "dow": pd.to_datetime(df["date"]).dt.dayofweek.reset_index(drop=True),
          "pdh": lev["PDH"], "pdl": lev["PDL"], "pdc": lev["PDC"], "pwh": lev["PWH"], "pwl": lev["PWL"], "pmh": lev["PMH"], "pml": lev["PML"],
          "fib_pivot_d": lev["FD_P"], "fib_r1_d": lev["FD_R1"], "fib_s1_d": lev["FD_S1"], "fib_pivot_w": lev["FW_P"], "fib_r1_w": lev["FW_R1"], "fib_s1_w": lev["FW_S1"],
          "fib_pivot_m": lev["FM_P"], "fib_r1_m": lev["FM_R1"], "fib_s1_m": lev["FM_S1"]}
    return ns


def run_expr(expr: str, df: pd.DataFrame, ns: dict | None = None) -> pd.Series:
    ns = ns or namespace(df); tree = validate(expr, set(ns))
    try: out = eval(compile(tree, "<scan>", "eval"), {"__builtins__": {}}, ns)              # nodes/functions whitelisted above, no builtins
    except Exception as e: raise ScanError(f"evaluation failed: {e!r}")
    if not isinstance(out, pd.Series): raise ScanError("expression must produce a series")
    s = out.fillna(False).astype(bool); s.index = df.index; return s
