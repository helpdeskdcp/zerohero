"""Tenant registry with hard isolation (G1). Own SQLite file; never chanakya.db.

* PAPER is the default for every tenant. LIVE_MANUAL / LIVE_AUTO are refused (and the
  refusal audited) until a separately approved gate implements live adapters.
* No credentials are stored: a tenant may carry only a `credential_ref` (name of an
  entry in a future secret store). The schema has no password/token/secret columns.
* Every read/write of tenant data goes through TenantScope, which is bound to ONE
  tenant_id; there is no API that returns another tenant's settings.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time

from . import config

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
STATUSES = ("ACTIVE", "SUSPENDED")


class TenantError(Exception):
    pass


class LiveModeNotEnabled(TenantError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
  tenant_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE',
  execution_mode TEXT NOT NULL DEFAULT 'PAPER', virtual_capital REAL NOT NULL,
  risk_json TEXT NOT NULL, entitlements_json TEXT NOT NULL, credential_ref TEXT,
  created_ts REAL NOT NULL, updated_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tenant_settings (
  tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id), key TEXT NOT NULL, value_json TEXT NOT NULL,
  updated_ts REAL NOT NULL, PRIMARY KEY (tenant_id, key));
CREATE TABLE IF NOT EXISTS tenant_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, tenant_id TEXT NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, detail_json TEXT NOT NULL);
"""
DEFAULT_RISK = {"max_daily_loss_pct": 2.0, "max_trade_loss_pct": 1.0, "max_open_positions": 1,
                "max_consecutive_losses": 4}
DEFAULT_ENTITLEMENTS = {"paper": True, "live_manual": False, "live_auto": False}


class TenantStore:
    def __init__(self, path: str | None = None):
        self.path = path or config.platform_db_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._c() as c:
            c.executescript(_SCHEMA)

    def _c(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    # -------------------------------------------------- admin-level operations
    def create_tenant(self, tenant_id: str, display_name: str, *, virtual_capital: float = 100000.0,
                      risk: dict | None = None, credential_ref: str | None = None, actor: str = "system") -> dict:
        if not _ID.match(tenant_id or ""):
            raise TenantError("tenant_id must be 2-40 chars of [a-z0-9_-], starting alphanumeric")
        if not display_name or not str(display_name).strip():
            raise TenantError("display_name required")
        if not (virtual_capital > 0):
            raise TenantError("virtual_capital must be > 0")
        r = {**DEFAULT_RISK, **(risk or {})}
        self._validate_risk(r)
        now = time.time()
        with self._c() as c:
            try:
                c.execute("INSERT INTO tenants (tenant_id, display_name, status, execution_mode, virtual_capital, "
                          "risk_json, entitlements_json, credential_ref, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (tenant_id, display_name.strip(), "ACTIVE", config.DEFAULT_EXECUTION_MODE, float(virtual_capital),
                           json.dumps(r), json.dumps(DEFAULT_ENTITLEMENTS), credential_ref, now, now))
            except sqlite3.IntegrityError:
                raise TenantError(f"tenant {tenant_id!r} already exists") from None
            self._audit(c, tenant_id, actor, "TENANT_CREATED", {"execution_mode": config.DEFAULT_EXECUTION_MODE})
        return self._get(tenant_id)

    def list_tenants(self) -> list[dict]:                      # admin only; returns no secrets by construction
        with self._c() as c:
            return [self._row(r) for r in c.execute("SELECT * FROM tenants ORDER BY tenant_id")]

    def set_execution_mode(self, tenant_id: str, mode: str, *, actor: str) -> dict:
        if mode not in config.EXECUTION_MODES:
            raise TenantError(f"unknown execution mode {mode!r}")
        t = self._get(tenant_id)
        if t is None:
            raise TenantError("unknown tenant")
        implemented = {"PAPER": True, "LIVE_MANUAL": config.LIVE_MANUAL_IMPLEMENTED, "LIVE_AUTO": config.LIVE_AUTO_IMPLEMENTED}
        with self._c() as c:
            if not implemented[mode]:
                self._audit(c, tenant_id, actor, "MODE_CHANGE_REFUSED", {"requested": mode, "reason": "live execution not implemented/approved"})
                c.commit()
                raise LiveModeNotEnabled(f"{mode} is not available: live execution has not been implemented or approved")
            c.execute("UPDATE tenants SET execution_mode=?, updated_ts=? WHERE tenant_id=?", (mode, time.time(), tenant_id))
            self._audit(c, tenant_id, actor, "MODE_CHANGED", {"from": t["execution_mode"], "to": mode})
        return self._get(tenant_id)

    def set_status(self, tenant_id: str, status: str, *, actor: str) -> dict:
        if status not in STATUSES:
            raise TenantError(f"unknown status {status!r}")
        if self._get(tenant_id) is None:
            raise TenantError("unknown tenant")
        with self._c() as c:
            c.execute("UPDATE tenants SET status=?, updated_ts=? WHERE tenant_id=?", (status, time.time(), tenant_id))
            self._audit(c, tenant_id, actor, "STATUS_CHANGED", {"to": status})
        return self._get(tenant_id)

    def audit_trail(self, tenant_id: str) -> list[dict]:
        with self._c() as c:
            return [{"ts": r["ts"], "actor": r["actor"], "action": r["action"], "detail": json.loads(r["detail_json"])}
                    for r in c.execute("SELECT * FROM tenant_audit WHERE tenant_id=? ORDER BY id", (tenant_id,))]

    def scope(self, tenant_id: str) -> "TenantScope":
        if self._get(tenant_id) is None:
            raise TenantError("unknown tenant")
        return TenantScope(self, tenant_id)

    # -------------------------------------------------- internals
    @staticmethod
    def _validate_risk(r: dict):
        for k in ("max_daily_loss_pct", "max_trade_loss_pct"):
            if not (0 < float(r[k]) <= 100):
                raise TenantError(f"{k} must be in (0, 100]")
        if int(r["max_open_positions"]) < 1 or int(r["max_consecutive_losses"]) < 1:
            raise TenantError("max_open_positions and max_consecutive_losses must be >= 1")

    @staticmethod
    def _audit(c, tenant_id, actor, action, detail):
        c.execute("INSERT INTO tenant_audit (ts, tenant_id, actor, action, detail_json) VALUES (?,?,?,?,?)",
                  (time.time(), tenant_id, actor, action, json.dumps(detail)))

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["risk"] = json.loads(d.pop("risk_json"))
        d["entitlements"] = json.loads(d.pop("entitlements_json"))
        return d

    def _get(self, tenant_id: str) -> dict | None:
        with self._c() as c:
            r = c.execute("SELECT * FROM tenants WHERE tenant_id=?", (tenant_id,)).fetchone()
        return self._row(r) if r else None


class TenantScope:
    """All tenant-owned data access, bound to a single tenant_id (no cross-tenant API)."""

    def __init__(self, store: TenantStore, tenant_id: str):
        self._s, self.tenant_id = store, tenant_id

    def profile(self) -> dict:
        return self._s._get(self.tenant_id)

    def put_setting(self, key: str, value) -> None:
        if not key or len(key) > 80:
            raise TenantError("invalid setting key")
        with self._s._c() as c:
            c.execute("INSERT INTO tenant_settings (tenant_id, key, value_json, updated_ts) VALUES (?,?,?,?) "
                      "ON CONFLICT(tenant_id, key) DO UPDATE SET value_json=excluded.value_json, updated_ts=excluded.updated_ts",
                      (self.tenant_id, key, json.dumps(value), time.time()))

    def get_setting(self, key: str, default=None):
        with self._s._c() as c:
            r = c.execute("SELECT value_json FROM tenant_settings WHERE tenant_id=? AND key=?", (self.tenant_id, key)).fetchone()
        return json.loads(r["value_json"]) if r else default

    def settings(self) -> dict:
        with self._s._c() as c:
            return {r["key"]: json.loads(r["value_json"]) for r in
                    c.execute("SELECT key, value_json FROM tenant_settings WHERE tenant_id=?", (self.tenant_id,))}
