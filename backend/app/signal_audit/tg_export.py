"""Reader for Telegram Desktop 'Export chat history' JSON (result.json). Handles single-chat and multi-chat exports, text as str or segment list, edits, replies.
Prefers `date_unixtime` (epoch); else treats the naive `date` as IST (override with tz_offset_sec). Reports id gaps (possible deleted messages)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

IST_SEC = 19800


def flat_text(t) -> str:
    if isinstance(t, str): return t
    if isinstance(t, list): return "".join(x if isinstance(x, str) else str(x.get("text", "")) for x in t)
    return ""


def load_export(path: str, tz_offset_sec: int = IST_SEC) -> tuple[list[dict], dict]:
    d = json.load(open(path, encoding="utf-8"))
    chats = d["chats"]["list"] if isinstance(d.get("chats"), dict) else None
    msgs = (chats[0]["messages"] if chats else d["messages"])
    out = []
    for m in msgs:
        if m.get("type") != "message": continue
        if m.get("date_unixtime"): t = int(m["date_unixtime"])
        else:
            t = int(datetime.fromisoformat(m["date"]).replace(tzinfo=timezone.utc).timestamp()) - tz_offset_sec
        et = None
        if m.get("edited_unixtime"): et = int(m["edited_unixtime"])
        elif m.get("edited"): et = int(datetime.fromisoformat(m["edited"]).replace(tzinfo=timezone.utc).timestamp()) - tz_offset_sec
        out.append({"id": int(m["id"]), "t": t, "date_ist": datetime.fromtimestamp(t + IST_SEC, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "text": flat_text(m.get("text")), "edited_t": et, "reply_to": m.get("reply_to_message_id")})
    out.sort(key=lambda x: x["t"])
    ids = sorted(x["id"] for x in out)
    gap = (ids[-1] - ids[0] + 1 - len(ids)) if ids else 0
    meta = {"messages": len(out), "id_gaps": gap, "id_gap_fraction": (gap / (ids[-1] - ids[0] + 1)) if ids else 0.0,
            "edited_messages": sum(1 for x in out if x["edited_t"]),
            "note": "id gaps = messages missing from the export (deleted by the channel, or non-message service ids); the export cannot tell which"}
    return out, meta


_HDR = __import__("re").compile(r"^\W*\[?(\d{1,4})[-./](\d{1,2})[-./](\d{2,4})[ ,T]+(\d{1,2}):(\d{2})(?::\d{2})?\]?[ ,\-:]*(.*)$")


def load_paste(path: str, tz_offset_sec: int = IST_SEC) -> tuple[list[dict], dict]:
    """Plain-text paste: a header line starts every message ('[2026-10-14 09:31] text', '14.10.2026 09:31 text', '2026-10-14T09:31 ...'); continuation lines join it.
    Date order: 4-digit first part = Y-M-D, else D-M-Y. Ids are synthetic (1..n) and reply links are unavailable in this format."""
    out, cur = [], None
    for line in open(path, encoding="utf-8").read().splitlines():
        m = _HDR.match(line)
        if m:
            a, b, c, hh, mm, rest = m.groups()
            y, mo, d = (int(a), int(b), int(c)) if len(a) == 4 else (int(c) + (2000 if int(c) < 100 else 0), int(b), int(a))
            t = int(datetime(y, mo, d, int(hh), int(mm), tzinfo=timezone.utc).timestamp()) - tz_offset_sec
            cur = {"id": len(out) + 1, "t": t, "date_ist": f"{y:04d}-{mo:02d}-{d:02d}", "text": rest, "edited_t": None, "reply_to": None}
            out.append(cur)
        elif cur is not None and line.strip():
            cur["text"] += "\n" + line
    meta = {"messages": len(out), "id_gaps": None, "id_gap_fraction": None, "edited_messages": None, "note": "pasted text: no ids/edits/deletions information is available"}
    return out, meta
