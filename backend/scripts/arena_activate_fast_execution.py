#!/usr/bin/env python3
"""Activate the broker-like execution delay of the paper contest (owner's decision 2026-10-26). Run ONCE, right AFTER the service was restarted with the new settle.py (older code ignores the keys).
Writes `latency_from_ts` + `latency_sec` and `exit_now_latency_from_ts` into data/arena/rule_effective.json (atomic, other keys untouched) and appends the dated entry to ARENA_RULE_CHANGES.md.
Effective instant = now: signals and management events stamped from then on get the short delay, everything earlier keeps 20 s (never retroactive; both sides).   python scripts/arena_activate_fast_execution.py [--sec 3] [--dry]"""
import json, os, sys, time
from datetime import datetime, timezone, timedelta
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); IST = timezone(timedelta(hours=5, minutes=30))
RULES = os.path.join(HERE, "data", "arena", "rule_effective.json"); CHANGES = os.path.join(HERE, "ARENA_RULE_CHANGES.md")


def activate(sec: float = 3.0, now: float | None = None, rules_path: str = RULES, changes_path: str | None = CHANGES) -> dict:
    now = now or time.time(); assert 0.5 <= sec <= 20, "the new delay must be between 0.5 and 20 s"
    d = json.load(open(rules_path)) if os.path.exists(rules_path) else {}
    d.update(latency_from_ts=now, latency_sec=float(sec), exit_now_latency_from_ts=now,
             latency_note=f"Broker-like execution delay {sec:g} s (was 20 s) for every signal and management event stamped at/after latency_from_ts; not retroactive; both sides (owner's decision 2026-10-26).")
    tmp = rules_path + ".tmp"; json.dump(d, open(tmp, "w"), indent=1); os.replace(tmp, rules_path)
    if changes_path:
        at = datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d %H:%M:%S")
        open(changes_path, "a", encoding="utf-8").write(f"\n## {at} IST — Broker-like execution delay: 20 s -> {sec:g} s (owner's decision; same for every participant and both sides)\n"
            f"- Why: participants measured the 20 s wait as the real lag of the system; a fast market moved 12 premium points inside it (an emergency exit pressed at +Rs 435 settled at -Rs 1,251). The delay is now {sec:g} s: BUY fills at the ASK and exits at the BID of the first stored quote at/after signal time + {sec:g} s (quotes are ~4 s apart under dense capture).\n"
            f"- Applies ONLY to signals and management events (amend / trail / exit / cancel) stamped at or after {at} IST (`latency_from_ts` in rule_effective.json); everything earlier keeps its original 20 s result. Never retroactive. The delay can only be shortened by a rule, never lengthened.\n"
            f"- Entry rules (ABOVE / NEAR / LIMIT / MARKET, the 5 % chase limit, DATA_GAP, INVALID_LEVELS) are unchanged.\n")
    return d


if __name__ == "__main__":
    sec = float(sys.argv[sys.argv.index("--sec") + 1]) if "--sec" in sys.argv else 3.0
    if "--dry" in sys.argv: print("dry run: would set latency_sec =", sec, "from now"); sys.exit(0)
    print(json.dumps(activate(sec), indent=1))
