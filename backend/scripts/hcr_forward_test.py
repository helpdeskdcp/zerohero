#!/usr/bin/env python3
"""
hcr_forward_test.py  --  RESEARCH ONLY. READ-ONLY.

Record one forward-test observation for the High-Conviction Runner strategy on a
NIFTY 5m session (default: the latest session with data). Appends a JSON line to
data/hcr_forward_test.jsonl and prints a human summary. No order, no live signal,
no change to any trading / frozen / calibration / broker / cron path.

  venv/bin/python scripts/hcr_forward_test.py            # latest session
  venv/bin/python scripts/hcr_forward_test.py 2026-09-07 # a specific session
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOG = ROOT / "data" / "hcr_forward_test.jsonl"

from app.research_strategy import hcr  # noqa: E402


def main():
    session = sys.argv[1] if len(sys.argv) > 1 else None
    rec = hcr.forward_test_record(session)
    if not rec.get("available"):
        print(f"[STOP] {rec.get('reason')}")
        sys.exit(2)
    rec["recorded_at"] = datetime.now(timezone.utc).isoformat()

    # dedup: replace any prior line for the same session
    lines = []
    if LOG.exists():
        for ln in LOG.read_text().splitlines():
            try:
                if json.loads(ln).get("session") != rec["session"]:
                    lines.append(ln)
            except ValueError:
                pass
    lines.append(json.dumps(rec, sort_keys=True))
    LOG.write_text("\n".join(lines) + "\n")

    p = print
    p("=" * 78)
    p(f"HCR FORWARD-TEST  --  {rec['session']} ({rec['dow']})   [READ-ONLY, PROXY, NOT VALIDATED]")
    p("=" * 78)
    p(f"  bars {rec['bars']}  {rec['first']}..{rec['last']} IST  "
      f"(session_complete={rec['session_complete']})")
    p(f"  day range {rec['day_range']}  vs 20d median {rec['day_range_median_20d']}  "
      f"-> wide? {rec['day_wide']} (need >= {rec['wide_threshold']})")
    p(f"  DoW ok (not Thu/Fri)? {rec['dow_ok']}   max 5m range_x before 14:00 = {rec['max_range_x_before_1400']}")
    p(f"  qualifying spikes (range_x >= {hcr.SPIKE_X}, < 14:00): {rec['qualifying_spikes'] or 'none'}")
    p("")
    if rec["hcr_fired"]:
        p(f"  >>> HCR FIRED  ({len(rec.get('trades', []))} trade(s))")
        for t in rec.get("trades", []):
            if t.get("outcome") == "TRIGGERED":
                p(f"      {t['spike_time']} range_x={t['range_x']}  {t['side']} @ {t['entry']} "
                  f"SL {t['sl']} R={t['R_pts']}pts  ->  legA {t['legA_R']}R / legB {t['legB_R']}R "
                  f"= blended {t['blended_R']}R  [{'GREEN' if t['green'] else 'RED'}]")
            else:
                p(f"      {t['spike_time']} range_x={t['range_x']}  -> {t.get('outcome')}")
        if "day_blended_R" in rec:
            p(f"      DAY blended R = {rec['day_blended_R']}  ->  {'GREEN' if rec['day_close_green'] else 'RED'}")
    else:
        p(f"  >>> HCR did NOT fire  --  {rec['reason_no_fire']}")
        p("      (expected: HCR is ~1.5 signals/month; most sessions it sits out)")
    p("")
    p(f"  appended -> {LOG}")

    # running tally
    recs = [json.loads(x) for x in LOG.read_text().splitlines() if x.strip()]
    fired = [r for r in recs if r.get("hcr_fired")]
    graded = [r for r in fired if "day_blended_R" in r]
    p("\n  --- forward-test tally to date ---")
    p(f"    sessions observed : {len(recs)}")
    p(f"    HCR fired         : {len(fired)}")
    if graded:
        greens = sum(1 for r in graded if r["day_close_green"])
        net = round(sum(r["day_blended_R"] for r in graded), 3)
        p(f"    graded fired days : {len(graded)}  close-green {greens}/{len(graded)}  net blended {net}R")
    else:
        p("    graded fired days : 0  (none fired-and-complete yet)")
    p("    NOTE: a real forward-test verdict needs dozens of fired signals across >=2 regimes.")


if __name__ == "__main__":
    main()
