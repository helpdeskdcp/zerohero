"""Cron entry: ingest whitelisted institution-channel posts into the arena ledger (ARENA_CHANNEL_IDS in .env; nothing is read if empty).
  python scripts/arena_channel_ingest.py            -> poll once
  python scripts/arena_channel_ingest.py --list     -> channels the bot has seen (title/type/id only; NOT contents)"""
import json, os, sys, urllib.parse, urllib.request
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for line in open(os.path.join(HERE, ".env")):
    if "=" in line and not line.startswith("#"): k, v = line.strip().split("=", 1); os.environ.setdefault(k, v)
from app.arena import api, channel_ingest as CI
tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if "--list" in sys.argv:
    seen = {}
    for x in json.load(urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/getUpdates?limit=100", timeout=15)).get("result", []):
        for k in ("channel_post", "my_chat_member", "edited_channel_post"):
            c = (x.get(k) or {}).get("chat")
            if c and c.get("type") == "channel": seen[str(c["id"])] = c.get("title")
    sys.exit(f"channels seen: {seen or 'none yet (owner must add the bot as admin, then post once)'}")
if not CI.channel_ids(): sys.exit(0)
c = api.cfg()
for r in CI.poll(tok, participant=c["name"], mode_fn=lambda: api.mode(c)): print(r["outcome"], r.get("signal_id", ""), r["key"])
