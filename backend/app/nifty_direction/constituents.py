"""Official NIFTY 50 constituent master. Nothing is hardcoded: members/ISIN/industry come from the NSE Indices CSV, weights only from an
explicit weights file. Weights are never estimated: if they are not complete the master reports weights_status != COMPLETE and the
contribution engine must refuse to score (fail closed)."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import time

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "nifty_direction")
CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
WEIGHT_TOL = 0.5          # |sum(weights) - 100| allowed (percentage points) for COMPLETE


def sha256(b: bytes) -> str: return hashlib.sha256(b).hexdigest()


def parse_constituents_csv(raw: bytes) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    need = {"Company Name", "Industry", "Symbol", "ISIN Code"}
    if not rows or not need <= set(rows[0]):
        raise ValueError("constituent CSV schema changed")
    return [{"symbol": r["Symbol"].strip(), "name": r["Company Name"].strip(), "sector": r["Industry"].strip(), "isin": r["ISIN Code"].strip()} for r in rows]


def parse_weights(raw: bytes) -> dict[str, float]:
    """Weights file: CSV `symbol,weight_pct[,as_of,source]`. Returns symbol -> pct."""
    out = {}
    for r in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
        out[r["symbol"].strip()] = float(r["weight_pct"])
    return out


def build_master(csv_raw: bytes, weights_raw: bytes | None = None, *, fetched_at: float | None = None) -> dict:
    members = parse_constituents_csv(csv_raw)
    syms = [m["symbol"] for m in members]
    if len(set(syms)) != len(syms): raise ValueError("duplicate symbols in constituent CSV")
    status, notes, w = "MISSING", [], {}
    if weights_raw is not None:
        w = parse_weights(weights_raw)
        unknown = sorted(set(w) - set(syms)); missing = sorted(set(syms) - set(w)); total = sum(w.values())
        if unknown: notes.append(f"weights for non-members: {unknown}")
        if missing: notes.append(f"no weight for: {missing}")
        status = "COMPLETE" if not unknown and not missing and abs(total - 100) <= WEIGHT_TOL else "INCOMPLETE"
        if status == "INCOMPLETE" and not missing and not unknown: notes.append(f"weights sum {total:.2f} not ~100")
    for m in members: m["weight_pct"] = w.get(m["symbol"]) if status == "COMPLETE" else None
    return {"count": len(members), "members": members, "weights_status": status, "weights_notes": notes,
            "csv_sha256": sha256(csv_raw), "weights_sha256": sha256(weights_raw) if weights_raw else None,
            "fetched_at": fetched_at or time.time(), "source": CSV_URL}


def validate(master: dict) -> list[str]:
    """Problems that must stop the engine; empty list = usable."""
    p = []
    if master.get("count") != 50: p.append(f"expected 50 constituents, got {master.get('count')}")
    if master.get("weights_status") != "COMPLETE": p.append(f"weights {master.get('weights_status')}: {master.get('weights_notes')}")
    return p


def save(master: dict, path: str | None = None) -> str:
    path = path or os.path.join(ROOT, "master.json"); os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"; json.dump(master, open(tmp, "w"), indent=1); os.replace(tmp, path); return path


def load(path: str | None = None) -> dict | None:
    try: return json.load(open(path or os.path.join(ROOT, "master.json")))
    except Exception: return None


def refresh(fetch=None, root: str | None = None) -> dict:
    """Download the official constituent CSV (kept as constituents.csv with its sha256), rebuild master.json using weights.csv ONLY if the operator supplied one."""
    root = root or ROOT; os.makedirs(root, exist_ok=True)
    if fetch is None:
        import requests
        fetch = lambda u: requests.get(u, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).content
    raw = fetch(CSV_URL); parse_constituents_csv(raw)                  # schema check before overwriting the cached copy
    open(os.path.join(root, "constituents.csv"), "wb").write(raw)
    wp = os.path.join(root, "weights.csv"); w = open(wp, "rb").read() if os.path.exists(wp) else None
    m = build_master(raw, w); save(m, os.path.join(root, "master.json")); return m


def current(root: str | None = None) -> dict | None:
    return load(os.path.join(root or ROOT, "master.json"))
