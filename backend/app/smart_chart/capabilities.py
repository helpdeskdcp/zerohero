"""Pine capability registry: what Smart Chart will and will not claim about a script.

Tiers (the only four values):
  supported    - exercised by OUR conformance tests against an independent reference (see VERIFIED + tests/test_smart_chart_pine.py)
  partial      - runs, but with a documented limitation (PARTIAL below, or upstream marks it "implemented, needs testing")
  not_tested   - listed as implemented by the pinned PineTS docs, but we have not verified it ourselves
  unsupported  - not listed as implemented upstream; a runtime failure on it is reported as an error

The upstream list is generated from the PINNED PineTS checkout (scripts/gen_pine_capabilities.py) and records the
version/commit it came from. Upstream docs can lag the code, so a call that is merely "not listed" is a WARNING; only
an actual runtime failure ("is not a function", undeclared identifier) is an ERROR (reported by the Node worker).
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))

# name -> evidence. tests/test_smart_chart_pine.py::test_verified_registry_is_backed_by_tests runs every entry.
VERIFIED: dict[str, str] = {
    "ta.sma": "matches numpy rolling mean", "ta.ema": "matches recursive EMA (alpha=2/(n+1), SMA seed)",
    "ta.rma": "matches Wilder smoothing", "ta.rsi": "matches Wilder RSI", "ta.atr": "matches Wilder ATR",
    "ta.highest": "matches rolling max", "ta.lowest": "matches rolling min", "ta.change": "matches diff",
    "ta.crossover": "matches reference cross detection", "ta.crossunder": "matches reference cross detection",
    "math.max": "matches max()", "math.abs": "matches abs()",
    "plot": "values aligned to bar times, warm-up is null", "plotshape": "boolean markers only where condition true",
    "hline": "constant level series", "input.int": "default + title/varId override",
    "strategy.entry": "market order fills at NEXT bar open (no look-ahead)", "strategy.close": "exit fills at next bar open",
    "strategy.exit": "stop/limit bracket fills at the level",
    "indicator": "declaration compiles and runs (every template/test)", "strategy": "declaration compiles and runs (every template/test)",
}

PARTIAL: dict[str, str] = {
    "request.security": "same chart symbol only; timeframe must be >= the chart timeframe and is derived from the loaded bars; other symbols are rejected",
    "request.security_lower_tf": "lower-timeframe data is not available (no intrabar source)",
    "strategy.exit": "intrabar order of stop vs limit within one bar is a bar-OHLC assumption (see backtest assumptions)",
}

NAMESPACES = {"ta", "math", "str", "array", "matrix", "map", "request", "strategy", "input", "color", "label", "line", "box",
              "table", "linefill", "polyline", "chart", "syminfo", "barstate", "timeframe", "ticker", "session", "runtime", "log"}
BARE_FUNCS = {"plot", "plotshape", "plotchar", "plotarrow", "plotbar", "plotcandle", "hline", "fill", "bgcolor", "barcolor", "alert",
              "alertcondition", "nz", "na", "fixnan", "indicator", "strategy", "input", "max", "min", "abs", "time", "hour", "minute",
              "dayofweek", "dayofmonth", "month", "year", "weekofyear", "timestamp", "time_close", "int", "float", "bool", "string"}


@lru_cache(maxsize=1)
def upstream() -> dict:
    with open(os.path.join(HERE, "capabilities_upstream.json"), encoding="utf-8") as f:
        return json.load(f)


def classify(name: str) -> dict:
    up = upstream()["functions"].get(name)
    if name in PARTIAL:
        tier, why = "partial", PARTIAL[name]
    elif name in VERIFIED:
        tier, why = "supported", VERIFIED[name]
    elif up in ("implemented_tested",):
        tier, why = "not_tested", "listed as implemented and tested upstream; not verified by Smart Chart tests"
    elif up == "implemented_untested":
        tier, why = "partial", "upstream marks it implemented but still needs testing"
    else:
        tier, why = "unsupported", "not listed as implemented in the pinned PineTS coverage tables"
    return {"name": name, "tier": tier, "note": why, "upstream": up or "unknown"}


_STR = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_COMMENT = re.compile(r"//.*$", re.M)
_CALL = re.compile(r"(?<![\w.])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(")
_UDF = re.compile(r"^\s*(?:export\s+)?(?:method\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*=>", re.M)


def _blank_strings(m: re.Match) -> str:
    return '"' + " " * (len(m.group(0)) - 2) + '"'


def scan(source: str) -> list[dict]:
    """Every distinct Pine library call in `source` (first line it appears on), classified. Strings/comments/user functions ignored."""
    cleaned = _COMMENT.sub(lambda m: " " * len(m.group(0)), source)
    cleaned = _STR.sub(_blank_strings, cleaned)
    udfs = set(_UDF.findall(cleaned))
    seen: dict[str, dict] = {}
    for lineno, line in enumerate(cleaned.splitlines(), 1):
        for m in _CALL.finditer(line):
            name = m.group(1)
            head = name.split(".")[0]
            if name in udfs or head in udfs:
                continue
            if "." in name:
                if head not in NAMESPACES:
                    continue                     # method call on a user variable (arr.push, m.get ...)
            elif name not in BARE_FUNCS:
                continue                         # user variable / unknown bare call: the compiler decides
            if name not in seen:
                c = classify(name)
                c["line"], c["column"] = lineno, m.start(1) + 1
                seen[name] = c
    return list(seen.values())


def diagnostics_for(source: str) -> list[dict]:
    """Static diagnostics: warnings only (an actual failure is reported by the runtime as an error)."""
    out = []
    for c in scan(source):
        if c["tier"] == "unsupported":
            out.append({"severity": "warning", "code": "NOT_LISTED_AS_SUPPORTED", "line": c["line"], "column": c["column"],
                        "message": f"{c['name']}() is {c['note']}; it may fail or return na"})
        elif c["tier"] == "partial":
            out.append({"severity": "info", "code": "PARTIAL_SUPPORT", "line": c["line"], "column": c["column"],
                        "message": f"{c['name']}(): {c['note']}"})
    return out


def registry() -> dict:
    up = upstream()
    names = sorted(up["functions"])
    items = [classify(n) for n in names]
    counts: dict[str, int] = {}
    for it in items:
        counts[it["tier"]] = counts.get(it["tier"], 0) + 1
    return {"pinets_version": up["pinets_version"], "pinets_commit": up["pinets_commit"], "tiers": counts,
            "verified": {k: v for k, v in VERIFIED.items()}, "partial": PARTIAL, "pine_versions": [5, 6],
            "not_supported_versions": [1, 2, 3, 4],
            "notice": "Practical compatibility with PineTS (an independent runtime). Not TradingView, not 100% compatible.",
            "functions": items}
