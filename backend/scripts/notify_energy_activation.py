"""Cron one-shot (every minute): when the first H-EV-NG activation row exists, send the owner (PRIVATE chat only) a plain summary, then remove its own cron line. Paper record, not a recommendation."""
import json, os, subprocess, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for line in open(os.path.join(HERE, ".env")):
    if line.startswith(("TELEGRAM_BOT_TOKEN=", "TELEGRAM_CHAT_ID=")):
        k, v = line.strip().split("=", 1); os.environ.setdefault(k, v)
P = os.path.join(HERE, "data", "energy_event", "activations.jsonl"); FLAG = P + ".notified"
if not os.path.exists(FLAG):
    if not os.path.exists(P) or not open(P).read().strip(): sys.exit(0)
    r = json.loads(open(P).readline())
    parts = [f"H-EV-NG first activation {r['date']}: {r['status']}"]
    for k in ("reason", "move_pct", "dir", "expiry", "strike", "ref_ask"):
        if r.get(k) is not None: parts.append(f"{k}={r[k]}")
    from app.connectors import telegram
    ok = telegram._send("\n".join([" | ".join(parts), "Paper shadow record (hash-chained), not a recommendation. Details: scripts/energy_event_report.py"]), os.environ.get("TELEGRAM_CHAT_ID")).get("ok")
    if not ok: sys.exit("owner notification failed")
    open(FLAG, "w").write("notified\n")
cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
subprocess.run(["crontab", "-"], input="\n".join(l for l in cur.splitlines() if "notify_energy_activation.py" not in l) + "\n", text=True)
