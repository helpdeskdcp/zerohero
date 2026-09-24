#!/usr/bin/env python3
"""
hedging_scan.py -- one app.hedging.runner.scan() tick.

Meant to run on a cron every 5 min during market hours. Monitors every OPEN
paper hedge position for exit rules regardless of armed state; evaluates and
opens new positions only when armed (see app/hedging/runner.py, POST
/api/hedging/arm). PAPER only, no broker call.

Usage:
  venv/bin/python scripts/hedging_scan.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.hedging.runner import scan  # noqa: E402


def main() -> int:
    out = scan()
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
