"""Stage 2a descriptive check on futures 1-minute bars (spec data/research/energy_event/SPEC_STAGE2_NG_2026-10-21.md section D). Read-only."""
import json, os, sqlite3
from datetime import datetime, timedelta
from collections import defaultdict
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = timedelta(hours=5, minutes=30)
UNCERTAIN = {"2026-09-09", "2026-09-10", "2026-10-14", "2026-10-15"}
db = sqlite3.connect(f"file:{os.path.join(HERE, 'data', 'market_history.db')}?mode=ro", uri=True, timeout=60)
days = defaultdict(dict)
for src, lo, hi in (("DHAN_HISTORICAL", "2026-06-30", "2026-08-31"), ("ANGELONE_CANDLES", "2026-09-01", "2026-10-20")):
    byd = defaultdict(lambda: defaultdict(list))
    for bs, d, o, c, ex in db.execute("SELECT bar_start,session_date_ist,o,c,expiry FROM market_candles WHERE symbol='NATURALGAS' AND kind='FUTURE' AND tf='1m' AND source=? AND session_date_ist>=? AND session_date_ist<=?", (src, lo, hi)):
        t = datetime.fromisoformat(bs.replace("Z", "+00:00")) + IST; byd[d][ex].append((t.hour * 60 + t.minute, o, c))
    for d, ex in byd.items():
        best = max(ex.values(), key=len); days[d] = {m: (o, c) for m, o, c in best}
R = 20 * 60; rows = []; skipped = []
for d in sorted(days):
    if datetime.strptime(d, "%Y-%m-%d").weekday() != 3 or d in UNCERTAIN: continue
    b = days[d]; need = [R, R + 1, R + 2, R + 3, R + 30]
    if not all(m in b for m in need): skipped.append(d); continue
    move = b[R + 2][1] / b[R][0] - 1
    if abs(move) < 0.003: rows.append({"date": d, "move_pct": round(100 * move, 3), "trigger": False}); continue
    s = 1 if move > 0 else -1; entry, exit_ = b[R + 3][0], b[R + 30][0]; ret = s * (exit_ / entry - 1)
    rows.append({"date": d, "move_pct": round(100 * move, 3), "trigger": True, "dir": "CE" if s > 0 else "PE", "signed_ret_pct": round(100 * ret, 3), "continued": ret > 0})
t = [r for r in rows if r["trigger"]]
out = {"n_event_days_used": len(rows), "skipped_missing_bars": skipped, "n_trigger": len(t), "continuation_rate": (sum(r["continued"] for r in t) / len(t)) if t else None,
       "mean_signed_ret_pct": (sum(r["signed_ret_pct"] for r in t) / len(t)) if t else None, "rows": rows}
out["decision_per_spec"] = "DROP (rate <= 50%)" if not t or out["continuation_rate"] <= 0.5 else "PROCEED to forward shadow only (not validated)"
d = os.path.join(HERE, "data", "research", "energy_event"); json.dump(out, open(os.path.join(d, "STAGE2A_RESULT_2026-10-21.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
