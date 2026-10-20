"""Find the private referee GROUP the bot has been added to and wire it as the evidence destination (hot: data/evidence_feed/chat_id.txt, no restart needed).
Bots cannot create groups. Steps: (1) create a private Telegram group with the referees, (2) add the bot, (3) post any message in the group, (4) run this script.
  python scripts/telegram_find_chat.py                 -> list group/supergroup chats the bot has seen (title, type, id shape; NOT message contents)
  python scripts/telegram_find_chat.py --set "Title"   -> use that group (refuses the signals channel, private chats, and anything that is not a group/supergroup), sends a connection test
  python scripts/telegram_find_chat.py --clear         -> back to the signals channel / private chat"""
import json, os, sys, urllib.request, urllib.parse
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from app.evidence_feed import publish as EV


def env(k):
    for line in open(os.path.join(HERE, ".env")):
        if line.startswith(k + "="): return line.strip().split("=", 1)[1]
    return ""


tok = env("TELEGRAM_BOT_TOKEN"); sig = env("TELEGRAM_SIGNALS_CHANNEL_ID"); priv = env("TELEGRAM_CHAT_ID")
call = lambda m, **q: json.load(urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/{m}" + (("?" + urllib.parse.urlencode(q)) if q else ""), timeout=15))
if "--clear" in sys.argv:
    if os.path.exists(EV.CHAT_FILE): os.remove(EV.CHAT_FILE)
    sys.exit("evidence chat cleared (falls back to signals channel / private chat)")
seen = {}
for x in call("getUpdates", limit=100, timeout=0).get("result", []):
    for k in ("message", "channel_post", "my_chat_member", "edited_message"):
        m = x.get(k)
        if m and m.get("chat"): c = m["chat"]; seen[str(c["id"])] = (c.get("type"), c.get("title") or "")
groups = {i: v for i, v in seen.items() if v[0] in ("group", "supergroup") and i not in (sig, priv)}
if "--set" in sys.argv:
    title = sys.argv[sys.argv.index("--set") + 1]; hit = [i for i, v in groups.items() if v[1] == title]
    if len(hit) != 1: sys.exit(f"no unique group titled {title!r}. Candidates: {[v[1] for v in groups.values()]}")
    os.makedirs(os.path.dirname(EV.CHAT_FILE), exist_ok=True); open(EV.CHAT_FILE, "w").write(hit[0])
    r = call("sendMessage", chat_id=hit[0], text="✅ ZeroHero evidence group connected. From now on the AI's PAPER signals (with timestamps) are posted here. Paper-trading evidence records, not recommendations.")
    print("evidence chat set; test message sent ok =", r.get("ok"))
else:
    print("groups the bot has seen:", [(v[1], v[0]) for v in groups.values()] or "none yet (add the bot to the group and post a message, then rerun)")
