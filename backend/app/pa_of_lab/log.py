"""Research log of every signal the chart shows (one JSON object per line). The API and this log are generated from the SAME payload, so markers == log by construction;
tests also re-derive the PA signals independently."""
from __future__ import annotations

import json
import os


def records(payload: dict) -> list[dict]:
    m = payload["meta"]; base = {"symbol": m["symbol"], "kind": m["kind"], "tf_sec": m["tf_sec"], "price_source": m["price_source"]}
    out = [dict(base, **s) for s in (payload["pa"] or {}).get("signals", [])]
    out += [dict(base, **s) for s in (payload["of"] or {}).get("signals", [])]
    out += [dict(base, source="combined", **c) for c in payload.get("combined", [])]
    return out


def write_jsonl(path: str, payload: dict) -> int:
    recs = records(payload)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        for r in recs: f.write(json.dumps(r, sort_keys=True, default=str) + "\n")
    return len(recs)
