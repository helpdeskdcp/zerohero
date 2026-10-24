"""Chartink alert webhook payload parser. Chartink posts JSON like
{"stocks": "SBIN,TCS", "trigger_prices": "812.5,3920.1", "triggered_at": "2:34 pm", "scan_name": "...", "scan_url": "...", "alert_name": "...", "webhook_url": "..."}.
Accepts JSON (strings or lists) and form-encoded bodies; never raises on odd input, returns {"error": ...} instead."""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs


def _split(v) -> list[str]:
    if v is None: return []
    if isinstance(v, (list, tuple)): return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in re.split(r"[,\s;]+", str(v)) if x.strip()]


def _num(x):
    try: return float(str(x).replace(",", ""))
    except Exception: return None


def parse_payload(body: bytes, content_type: str = "") -> dict:
    try:
        text = body.decode("utf-8", "replace").strip()
        if not text: return {"error": "empty body"}
        d = None
        if "json" in (content_type or "").lower() or text[:1] in "{[":
            try: d = json.loads(text)
            except Exception: d = None
        if d is None: d = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(text).items()}
        if not isinstance(d, dict): return {"error": "payload is not an object"}
        syms = [s.upper() for s in _split(d.get("stocks") or d.get("symbols") or d.get("stock"))]
        prices = [_num(x) for x in _split(d.get("trigger_prices") or d.get("prices") or d.get("trigger_price"))]
        prices += [None] * (len(syms) - len(prices))
        seen, rows = set(), []
        for s, p in zip(syms, prices):
            if s in seen: continue
            seen.add(s); rows.append({"symbol": s, "trigger_price": p})
        scan = str(d.get("scan_name") or d.get("alert_name") or "UNKNOWN").strip()[:160]
        return {"scan_name": scan, "scan_url": str(d.get("scan_url") or "")[:160], "alert_name": str(d.get("alert_name") or "")[:200], "triggered_at_text": str(d.get("triggered_at") or "")[:40],
                "symbols": rows, "extra_keys": sorted(k for k in d if k not in ("stocks", "symbols", "stock", "trigger_prices", "prices", "trigger_price", "scan_name", "scan_url", "alert_name", "triggered_at", "webhook_url"))}
    except Exception as e:
        return {"error": f"parse failed: {e!r}"}
