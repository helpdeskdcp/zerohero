"""ONE-TIME interactive login for the read-only channel reader. Run it yourself:   ! venv/bin/python scripts/telethon_login.py
You type the phone number, the login code Telegram sends you and (if set) the 2FA password. NOTHING is stored except the session file data/tg/reader.session (chmod 600, gitignored).
Afterwards it lists ONLY channels (title, id) so you can pick the verified numeric ids. Use a dedicated Telegram account that has joined just the source channels."""
import os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for line in open(os.path.join(HERE, ".env")):
    if "=" in line and not line.startswith("#"): k, v = line.strip().split("=", 1); os.environ.setdefault(k, v)
from telethon.sync import TelegramClient
SESSION = os.path.join(HERE, "data", "tg", "reader")
with TelegramClient(SESSION, int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]) as c:
    me = c.get_me(); print("logged in as account id", me.id)
    print("channels this account has joined (id, title):")
    for d in c.iter_dialogs():
        if d.is_channel and not d.is_group: print(f"  {d.entity.id and int('-100' + str(d.entity.id))}\t{d.title}")
for suf in ("", "-journal"):
    p = SESSION + ".session" + suf
    if os.path.exists(p): os.chmod(p, 0o600)
