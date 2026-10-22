"""Run by cron on the official start date (00:05 IST): saves the rehearsal results, moves the scoring window start to the official start, logs it with the real instant, tells the owner (private chat) and removes its own cron line.
Safe to run any day: before the official start date it does nothing."""
import json, os, subprocess, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for l in open(os.path.join(HERE, ".env")):
    if "=" in l and not l.startswith("#"): k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
from datetime import datetime, timedelta, timezone
from app.arena import cutover
IST = timezone(timedelta(hours=5, minutes=30)); now = datetime.now(IST); today = now.strftime("%Y-%m-%d"); path = os.path.join(HERE, "data", "arena", "contest.json")
c = json.load(open(path)); off = c.get("official_start")
if not off or today < off: sys.exit(0)
if c.get("start") != off:
    prev = (datetime.fromisoformat(off) - timedelta(days=1)).strftime("%Y-%m-%d")
    rep = subprocess.run([sys.executable, os.path.join(HERE, "scripts", "arena_daily_report.py"), "--date", prev], capture_output=True, text=True, cwd=HERE).stdout        # rehearsal results, computed under the OLD window
    open(os.path.join(HERE, "data", "arena", "REHEARSAL_FINAL.md"), "w").write(f"# Rehearsal results (scored under the rehearsal window {c.get('start')} .. {prev}; not part of the official contest)\n\n" + rep)
    r = cutover.apply(path, today)
    open(os.path.join(HERE, "ARENA_RULE_CHANGES.md"), "a").write(f"| {now.strftime('%Y-%m-%d %H:%M IST')} | **Official contest starts.** The scoring window now starts on {off} (was {r['old_start']}, the rehearsal). Rehearsal signals stay visible but no longer count; their final standings are saved in data/arena/REHEARSAL_FINAL.md. Excluded tests (H00001, H00007) stay excluded. | The project owner fixed the official start at {off} after a week of rehearsal (2026-10-22 .. {prev}). No settlement rule changed. |\n")
    try:
        from app.connectors import telegram
        telegram._send(f"Arena: the official contest has started ({off}). Rehearsal results saved (data/arena/REHEARSAL_FINAL.md). Scoring window now {off} .. {c.get('end')}.", os.environ.get("TELEGRAM_CHAT_ID"))
    except Exception: pass
cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
subprocess.run(["crontab", "-"], input="\n".join(l for l in cur.splitlines() if "arena_cutover.py" not in l) + "\n", text=True)
