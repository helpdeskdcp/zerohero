"""Scanner: verifies rules.yaml against the code (READ-ONLY; imports modules only to read constants, never calls anything).
Checks per rule: file exists, symbol defined (ast), every param source resolves and equals the registered value, evidence refs exist.
Global: every environment variable read under app/ is classified (pattern or explicit), same-concept parameters with DIFFERENT values across enforced rules are reported."""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import re

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))              # backend/
APP = os.path.join(ROOT, "app")
ENFORCED = {"ENFORCED"}


def load_registry(path: str | None = None) -> dict:
    return yaml.safe_load(open(path or os.path.join(HERE, "rules.yaml"), encoding="utf-8"))


def _rel(p: str) -> str: return os.path.join(ROOT, p)


def symbol_exists(file: str, symbol: str) -> bool:
    try: tree = ast.parse(open(_rel(file), encoding="utf-8").read())
    except Exception: return False
    name = symbol.split(".")[-1]
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name: return True
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets): return True
    return False


def _num(s: str):
    try: return float(s)
    except ValueError: return s


def resolve_param(src: str):
    """-> (ok, value, error)"""
    try:
        kind, rest = src.split(":", 1)
        if kind == "py":
            mod, attr = rest.split(":", 1); parts = attr.split("."); v = getattr(importlib.import_module(mod), parts[0])
            for k in parts[1:]: v = v[k]
            return True, v, None
        if kind == "sig":
            mod, call = rest.split(":", 1); fn, arg = re.match(r"(\w+)\((\w+)\)", call).groups()
            d = inspect.signature(getattr(importlib.import_module(mod), fn)).parameters[arg].default
            return (d is not inspect.Parameter.empty), d, None
        if kind in ("json", "yaml"):
            f, path = rest.split("#", 1); v = (json.load if kind == "json" else yaml.safe_load)(open(_rel(f), encoding="utf-8"))
            for k in path.split("."): v = v[k]
            return True, v, None
        if kind == "re":
            f, rx = rest.split("::", 1); m = re.search(rx, open(_rel(f), encoding="utf-8").read(), re.M)
            return (m is not None), (_num(m.group("v")) if m else None), (None if m else "regex not found")
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"
    return False, None, "unknown source kind"


def same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool): return a is b or a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)): return float(a) == float(b)
    return a == b


def env_scan() -> dict[str, dict]:
    """name -> {defaults:[...], where:[file:line]} for os.environ.get / os.getenv / os.environ[...] with a constant name, under app/."""
    out: dict[str, dict] = {}
    for dp, _, fs in os.walk(APP):
        if "__pycache__" in dp: continue
        for f in fs:
            if not f.endswith(".py"): continue
            p = os.path.join(dp, f)
            try: tree = ast.parse(open(p, encoding="utf-8").read())
            except Exception: continue
            for n in ast.walk(tree):
                name = default = None
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                    fn = n.func
                    is_get = fn.attr == "get" and isinstance(fn.value, ast.Attribute) and fn.value.attr == "environ"
                    is_getenv = fn.attr == "getenv" and isinstance(fn.value, ast.Name) and fn.value.id == "os"
                    if is_get or is_getenv:
                        name = n.args[0].value
                        default = ast.literal_eval(n.args[1]) if len(n.args) > 1 and isinstance(n.args[1], ast.Constant) else (None if len(n.args) < 2 else "<expr>")
                elif isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute) and n.value.attr == "environ" and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
                    name, default = n.slice.value, "<required>"
                if name and re.match(r"^[A-Z][A-Z0-9_]{2,}$", name):
                    e = out.setdefault(name, {"defaults": [], "where": []})
                    if default not in e["defaults"]: e["defaults"].append(default)
                    e["where"].append(f"{os.path.relpath(p, ROOT)}:{n.lineno}")
    return out


def classify_env(name: str, reg: dict) -> dict | None:
    for e in reg.get("env_flags") or []:
        if e["name"] == name: return e
    for p in reg.get("env_patterns") or []:
        if re.search(p["re"], name): return {"name": name, "category": p["category"]}
    return None


def scan(path: str | None = None) -> dict:
    reg = load_registry(path); rules, problems = [], []
    ids = [r["id"] for r in reg["rules"]]
    for d in {i for i in ids if ids.count(i) > 1}: problems.append(f"duplicate rule id {d}")
    for r in reg["rules"]:
        chk = {"file_exists": os.path.exists(_rel(r["where"]["file"])), "symbol_exists": None, "params": [], "evidence_refs": []}
        chk["symbol_exists"] = symbol_exists(r["where"]["file"], r["where"]["symbol"]) if chk["file_exists"] and r["where"].get("symbol") else None
        for p in r.get("params") or []:
            ok, val, err = resolve_param(p["source"])
            chk["params"].append({"name": p["name"], "registered": p["value"], "actual": val, "resolved": ok, "match": bool(ok and same(val, p["value"])), "error": err})
        for ref in (r.get("evidence") or {}).get("refs") or []:
            chk["evidence_refs"].append({"ref": ref, "exists": os.path.exists(_rel(ref))})
        bad = (not chk["file_exists"]) or chk["symbol_exists"] is False or any(not x["match"] for x in chk["params"]) or any(not x["exists"] for x in chk["evidence_refs"])
        rules.append({**r, "checks": chk, "ok": not bad})
        if bad: problems.append(f"{r['id']}: drift (see checks)")
    env = env_scan(); env_rows, unclassified = [], []
    for n in sorted(env):
        c = classify_env(n, reg)
        if c is None: unclassified.append(n)
        env_rows.append({"name": n, "category": (c or {}).get("category"), "rule": (c or {}).get("rule"), "defaults": env[n]["defaults"], "where": env[n]["where"][:3]})
    by: dict[str, list] = {}
    for r in reg["rules"]:
        for p in r.get("params") or []: by.setdefault(p["concept"], []).append({"rule": r["id"], "value": p["value"], "unit": p.get("unit"), "mode": r["mode"]})
    conflicts = []
    for c, items in sorted(by.items()):
        vals = []
        for it in items:
            if not any(same(it["value"], v) for v in vals): vals.append(it["value"])
        if len(vals) > 1:
            enforced = [i for i in items if i["mode"] in ENFORCED]
            units = {i["unit"] for i in enforced}
            ev = []
            for i in enforced:
                if not any(same(i["value"], v) for v in ev): ev.append(i["value"])
            sev = "INFO" if len(ev) <= 1 else ("REVIEW_UNITS" if len(units) > 1 else "ENFORCED_CONFLICT")
            conflicts.append({"concept": c, "values": vals, "items": items, "severity": sev})
    return {"registry_version": "2026-10-20", "rules": rules, "env": env_rows, "unclassified_env": unclassified, "conflicts": conflicts, "problems": problems,
            "not_yet_inventoried": reg.get("not_yet_inventoried") or []}
