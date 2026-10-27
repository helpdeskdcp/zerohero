"""Smart Chart limits. Every value can be tightened/relaxed with a SMARTCHART_* env var (read at import)."""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def db_path() -> str:
    return os.environ.get("SMART_CHART_DB") or os.path.join(BACKEND, "data", "smart_chart.db")


MAX_SCRIPT_BYTES = _int("SMARTCHART_MAX_SCRIPT_BYTES", 64_000)
MAX_SCRIPTS_PER_OWNER = _int("SMARTCHART_MAX_SCRIPTS", 200)
MAX_VERSIONS_PER_SCRIPT = _int("SMARTCHART_MAX_VERSIONS", 50)
MAX_CANDLES = _int("SMARTCHART_MAX_CANDLES", 10_000)            # per response / per script run
DEFAULT_CANDLES = _int("SMARTCHART_DEFAULT_CANDLES", 1_500)
EXEC_TIMEOUT_SEC = _float("SMARTCHART_EXEC_TIMEOUT", 25.0)
NODE_HEAP_MB = _int("SMARTCHART_NODE_HEAP_MB", 512)
NODE_CPU_SEC = _int("SMARTCHART_NODE_CPU_SEC", 40)
MAX_CONCURRENT_RUNS = _int("SMARTCHART_MAX_CONCURRENT_RUNS", 2)
RUNS_PER_MINUTE = _int("SMARTCHART_RUNS_PER_MINUTE", 30)         # per owner, compile + run + backtest
MAX_RUNNER_OUTPUT_BYTES = _int("SMARTCHART_MAX_OUTPUT_BYTES", 24_000_000)
CACHE_MAX_ENTRIES = _int("SMARTCHART_CACHE_ENTRIES", 24)
CACHE_MAX_BARS_PER_ENTRY = _int("SMARTCHART_CACHE_BARS", 80_000)
LIVE_TAIL_TTL_SEC = _float("SMARTCHART_LIVE_TTL", 3.0)           # min gap between Upstox tail refreshes per instrument
UPSTOX_MIN_GAP_SEC = _float("SMARTCHART_UPSTOX_GAP", 0.15)       # global spacing between Upstox calls
PAPER_MAX_PORTFOLIOS = _int("SMARTCHART_PAPER_MAX", 10)
EXEC_JOB_RETENTION = _int("SMARTCHART_JOB_RETENTION", 500)       # exec_jobs rows kept per owner
BACKTEST_RETENTION = _int("SMARTCHART_BACKTEST_RETENTION", 100)  # backtests kept per owner
