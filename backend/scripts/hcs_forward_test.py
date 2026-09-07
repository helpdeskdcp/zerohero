#!/usr/bin/env python3
"""
hcs_forward_test.py -- READ-ONLY.

  (no arg)         : replay the HCS A+ gate over all resolved autoscalp signals
  YYYY-MM-DD       : also record that session's live A+ signals to
                     data/hcs_forward_test.jsonl (append-only)

Nothing here trades, emits a signal, or changes any engine.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.hcs import forward_test as ft  # noqa: E402


def main():
    r = ft.replay()
    p = print
    p("=" * 84)
    p("HCS FORWARD-TEST  --  A+ gate replay over resolved autoscalp signals  [READ-ONLY]")
    p("=" * 84)
    if not r.get("available"):
        p(f"  [STOP] {r.get('reason')}")
        return
    g = r["gate"]
    p(f"  gate: HCS>={g['hcs_min']} , calib p>={g['prob_min']} , conf in {g['allowed_confidence']} , 0 hard vetoes")
    p(f"  {r['limitation']}")
    for name in ("all_resolved", "hcs_a_plus", "hcs_rejected"):
        m = r[name]
        if m.get("n"):
            p(f"  {name:<14} n={m['n']:>3}  win {m['win_rate']*100:>5.1f}%  "
              f"E[R~] {m['expectancy_R_proxy']:>+5.2f}  avgPts {m['avg_points']}  ses={m['sessions']}")
        else:
            p(f"  {name:<14} n=0")
    a, b = r["a_plus_first_half"], r["a_plus_second_half"]
    p(f"  A+ chrono   1st half n={a.get('n',0)} win {(a.get('win_rate') or 0)*100:.0f}%   "
      f"2nd half n={b.get('n',0)} win {(b.get('win_rate') or 0)*100:.0f}%")
    p("\n  A+ signals:")
    for s in r["a_plus_signals"]:
        p(f"    {s['session']} {s['symbol']:<11} {s['decision']:<7} hcs={s['hcs_score']} "
          f"p={s['calib_p']} {s['confidence']:<7} -> {s['outcome']} ({s['points']})")
    p(f"\n  VERDICT: {r['verdict']}")

    if len(sys.argv) > 1:
        session = sys.argv[1]
        rec = ft.record_live(session)
        p(f"\n  recorded live A+ for {session}: {rec['a_plus_count']} signal(s) -> {rec['log']}")
        for x in rec["a_plus"]:
            p(f"    {x['symbol']} {x['decision']} hcs={x['hcs_score']} p={x['calib_p']} {x['confidence']}")
        s = ft.log_summary()
        p(f"  log: {s['observations']} observation(s), {s['a_plus_total']} live A+ total")


if __name__ == "__main__":
    main()
