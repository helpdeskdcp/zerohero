"""One-shot notifier logic: when the FIRST evidence post exists in the hash-chained list, build a private-chat message for the owner (an operational note, not a signal)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def first_post(path: str) -> dict | None:
    if not os.path.exists(path): return None
    for line in open(path, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("type") == "EVIDENCE_POST": return r
    return None


def build_message(rec: dict) -> str:
    t = datetime.fromtimestamp(rec.get("signal_ts") or 0, tz=IST).strftime("%Y-%m-%d %H:%M:%S IST")
    tg = rec.get("telegram") or {}
    head = "✅ ZeroHero AI cha pahila signal evidence group madhe post zala" if tg.get("ok") else "⚠ AI cha pahila signal tayar zala pan evidence group madhe post FAIL zala"
    lines = [head, f"{rec.get('kind')} · ID {rec.get('id')} · {t}"]
    first = [ln for ln in str(rec.get("text", "")).splitlines() if "·" in ln or "Entry" in ln][:3]
    lines += [ln.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "") for ln in first]
    lines.append(f"Telegram: ok={tg.get('ok')} message_id={tg.get('message_id')}" + (f" reason={tg.get('reason')}" if tg.get("reason") else ""))
    return "\n".join(lines)
