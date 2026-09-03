"""
CLI for the Smart Index Scalper replay (slice 5/6).

    python -m app.smart_index_scalper replay-sessions [--symbols NIFTY,SENSEX]
    python -m app.smart_index_scalper replay [--symbols ...] [--step 3]
                                             [--profile BALANCED ...] [--max-hold 25]

RESEARCH ONLY. Strict-causal replay over data/market_history.db. No order path.
"""
from __future__ import annotations

import argparse
import json

from .replay import SmartScalperReplay


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.smart_index_scalper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("replay-sessions", help="list replayable (instrument, session) pairs")
    p1.add_argument("--symbols", default=None)

    p2 = sub.add_parser("replay", help="run the strict-causal replay + metrics")
    p2.add_argument("--symbols", default=None)
    p2.add_argument("--step", type=int, default=3, help="minutes between evaluations")
    p2.add_argument("--profile", action="append", default=None,
                    help="repeatable; default = all three profiles")
    p2.add_argument("--max-hold", type=int, default=25)
    p2.add_argument("--trades", type=int, default=20, help="how many trade rows to print")

    a = ap.parse_args(argv)
    r = SmartScalperReplay()
    if a.cmd == "replay-sessions":
        print(json.dumps(r.available_sessions(a.symbols), indent=2, default=str))
        return 0

    out = r.run(a.symbols, step_min=a.step, profiles=a.profile, max_hold_min=a.max_hold)
    out["trades"] = out["trades"][:max(0, a.trades)]
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
