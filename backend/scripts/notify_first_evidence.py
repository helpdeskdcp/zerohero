"""Cron one-shot (every minute): the first time an AI evidence post exists, send the owner a private-chat note, then remove its own cron line. Idempotent via a flag file."""
import os, subprocess, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for line in open(os.path.join(HERE, ".env")):
    if line.startswith(("TELEGRAM_BOT_TOKEN=", "TELEGRAM_CHAT_ID=")):
        k, v = line.strip().split("=", 1); os.environ.setdefault(k, v)
from app.evidence_feed import first_post as FP
D = os.path.join(HERE, "data", "evidence_feed"); FLAG = os.path.join(D, "first_post_notified.flag")
if not os.path.exists(FLAG):
    rec = FP.first_post(os.path.join(D, "posts.jsonl"))
    if rec is None: sys.exit(0)
    from app.connectors import telegram
    r = telegram._send(FP.build_message(rec), os.environ.get("TELEGRAM_CHAT_ID"))
    if r.get("ok"): open(FLAG, "w").write("notified\n")
    else: sys.exit(f"owner notification failed: {r}")
cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
keep = "\n".join(l for l in cur.splitlines() if "notify_first_evidence.py" not in l) + "\n"
subprocess.run(["crontab", "-"], input=keep, text=True)
