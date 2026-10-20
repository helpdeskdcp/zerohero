"""Fail-closed rules engine. Policy is validated at load (restricted expressions, paper-only, every L_ name defined). evaluate(ctx) -> Decision with every rule's outcome.
on_missing='block' (live/shadow default): a BLOCK rule whose input is missing BLOCKS (MISSING_INPUT). on_missing='skip' (replay of historical data): the rule is reported NOT_EVALUABLE."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field

import yaml

from .expr import ExprError, MissingInput, compile_expr

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT_DB = os.environ.get("RULES_ENGINE_AUDIT_DB", os.path.join(os.path.dirname(os.path.dirname(HERE)), "data", "rules_engine_audit.db"))


class PolicyError(ValueError): pass


@dataclass
class Decision:
    allow: bool
    blocked_by: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    not_evaluable: list = field(default_factory=list)
    evaluated: list = field(default_factory=list)
    policy_version: str = ""
    validation_status: str = ""
    ctx_hash: str = ""

    def to_dict(self): return self.__dict__.copy()


class Engine:
    def __init__(self, policy: dict):
        self.p = self._validate(policy)
        self.limits = {f"L_{k}": v for k, v in self.p["limits"].items()}
        self.rules = [{**r, "_run": compile_expr(r["when"])} for r in self.p["rules"]]

    @classmethod
    def load(cls, path: str | None = None) -> "Engine":
        return cls(yaml.safe_load(open(path or os.path.join(HERE, "policy.yaml"), encoding="utf-8")))

    @staticmethod
    def _validate(p: dict) -> dict:
        def need(c, m):
            if not c: raise PolicyError(m)
        need(isinstance(p, dict), "policy must be a mapping")
        for k in ("policy_version", "validation_status", "execution", "limits", "rules"): need(k in p, f"missing {k}")
        need(p["execution"].get("mode") == "PAPER" and p["execution"].get("live_orders_enabled") is False, "policy must be PAPER with live_orders_enabled false")
        ids = [r.get("id") for r in p["rules"]]; need(len(ids) == len(set(ids)) and all(ids), "rule ids must be unique and non-empty")
        for r in p["rules"]:
            need(r.get("action") in ("BLOCK", "WARN"), f"{r.get('id')}: action must be BLOCK or WARN")
            need(isinstance(r.get("reason"), str) and r["reason"], f"{r['id']}: reason required")
            try: names = compile_expr(r["when"]).names
            except ExprError as e: raise PolicyError(f"{r['id']}: {e}")
            for n in names:
                if n.startswith("L_"): need(n[2:] in p["limits"], f"{r['id']}: undefined limit {n}")
        need(any(r["id"] == "R-LIVE-NEVER" and r["action"] == "BLOCK" for r in p["rules"]), "R-LIVE-NEVER block rule is mandatory")
        for k, v in p["limits"].items(): need(isinstance(v, (int, float)) and not isinstance(v, bool), f"limit {k} must be numeric")
        return p

    def evaluate(self, ctx: dict, *, on_missing: str = "block", audit: bool = False) -> Decision:
        assert on_missing in ("block", "skip")
        full = {**ctx, **self.limits}
        d = Decision(True, policy_version=str(self.p["policy_version"]), validation_status=self.p["validation_status"],
                     ctx_hash=hashlib.sha256(json.dumps(ctx, sort_keys=True, default=str).encode()).hexdigest()[:16])
        for r in self.rules:
            try: hit = bool(r["_run"](full)); status = "HIT" if hit else "PASS"
            except MissingInput as m:
                status = "NOT_EVALUABLE"; hit = (on_missing == "block" and r["action"] == "BLOCK")
                if on_missing == "block": status = "MISSING_INPUT"
                d.not_evaluable.append({"rule": r["id"], "missing": str(m.args[0])})
            except ZeroDivisionError: status, hit = "ERROR", r["action"] == "BLOCK"          # fail closed
            d.evaluated.append({"rule": r["id"], "status": status})
            if hit and r["action"] == "BLOCK": d.allow = False; d.blocked_by.append({"rule": r["id"], "reason": r["reason"] if status == "HIT" else f"{r['reason']} (unevaluable: {status})"})
            elif hit and r["action"] == "WARN": d.warnings.append({"rule": r["id"], "reason": r["reason"]})
        if audit: _audit(d, ctx)
        return d


def _audit(d: Decision, ctx: dict, path: str | None = None):
    p = path or AUDIT_DB; os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p, timeout=10)
    try:
        c.execute("CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, policy_version TEXT, ctx_hash TEXT, allow INTEGER, blocked_by TEXT, warnings TEXT, ctx TEXT)")
        c.execute("INSERT INTO decisions (ts,policy_version,ctx_hash,allow,blocked_by,warnings,ctx) VALUES (?,?,?,?,?,?,?)",
                  (time.time(), d.policy_version, d.ctx_hash, int(d.allow), json.dumps(d.blocked_by), json.dumps(d.warnings), json.dumps(ctx, default=str)))
        c.commit()
    finally:
        c.close()
