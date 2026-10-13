"""G1 platform foundation: tenancy isolation, PAPER-by-default, live refused, no credentials, isolation from prod."""
import ast
import glob
import importlib
import os
import sqlite3
import sys

import pytest

from app.platform import config, events
from app.platform.tenancy import LiveModeNotEnabled, TenantError, TenantStore

PKG = os.path.join(os.path.dirname(__file__), "..", "app", "platform")


@pytest.fixture
def store(tmp_path):
    return TenantStore(str(tmp_path / "p.db"))


def test_new_tenant_is_paper_with_live_entitlements_off(store):
    t = store.create_tenant("alice", "Alice")
    assert t["execution_mode"] == "PAPER" and t["status"] == "ACTIVE"
    assert t["entitlements"] == {"paper": True, "live_manual": False, "live_auto": False}
    assert t["risk"]["max_open_positions"] == 1 and t["virtual_capital"] == 100000.0


def test_live_modes_are_refused_and_the_refusal_is_audited(store):
    store.create_tenant("alice", "Alice")
    for mode in ("LIVE_MANUAL", "LIVE_AUTO"):
        with pytest.raises(LiveModeNotEnabled):
            store.set_execution_mode("alice", mode, actor="admin")
    assert store.list_tenants()[0]["execution_mode"] == "PAPER"
    acts = [(a["action"], a["detail"].get("requested")) for a in store.audit_trail("alice")]
    assert ("MODE_CHANGE_REFUSED", "LIVE_MANUAL") in acts and ("MODE_CHANGE_REFUSED", "LIVE_AUTO") in acts
    assert store.set_execution_mode("alice", "PAPER", actor="admin")["execution_mode"] == "PAPER"
    assert config.LIVE_MANUAL_IMPLEMENTED is False and config.LIVE_AUTO_IMPLEMENTED is False


def test_no_environment_variable_can_enable_live(store, monkeypatch):
    for k in ("CHANAKYA_ALLOW_LIVE", "CHANAKYA_LIVE_CONFIRM_TOKEN", "PLATFORM_ALLOW_LIVE"):
        monkeypatch.setenv(k, "1")
    store.create_tenant("alice", "Alice")
    with pytest.raises(LiveModeNotEnabled):
        store.set_execution_mode("alice", "LIVE_AUTO", actor="admin")


def test_tenant_isolation_settings_never_cross(store):
    store.create_tenant("alice", "Alice"); store.create_tenant("bob", "Bob")
    a, b = store.scope("alice"), store.scope("bob")
    a.put_setting("lot_size", 75); b.put_setting("lot_size", 25); a.put_setting("only_a", True)
    assert a.get_setting("lot_size") == 75 and b.get_setting("lot_size") == 25
    assert b.get_setting("only_a") is None and "only_a" not in b.settings()
    assert b.profile()["tenant_id"] == "bob" and a.profile()["tenant_id"] == "alice"
    with pytest.raises(TenantError):
        store.scope("mallory")


def test_events_visibility_shared_vs_private():
    shared = events.PlatformEvent("TICK", {"p": 1})
    private = events.PlatformEvent("TENANT_SIGNAL", {"x": 1}, tenant_id="alice")
    assert shared.visible_to("alice") and shared.visible_to("bob")
    assert private.visible_to("alice") and not private.visible_to("bob")
    with pytest.raises(ValueError):
        events.PlatformEvent("ORDER_PLACED", {})


def test_validation_and_duplicates(store):
    for bad in ("A", "x", "UPPER", "has space", "-lead", "a" * 41):
        with pytest.raises(TenantError):
            store.create_tenant(bad, "x")
    with pytest.raises(TenantError):
        store.create_tenant("ok1", "n", virtual_capital=0)
    with pytest.raises(TenantError):
        store.create_tenant("ok2", "n", risk={"max_daily_loss_pct": 0})
    store.create_tenant("ok3", "n")
    with pytest.raises(TenantError):
        store.create_tenant("ok3", "n")


def test_schema_has_no_credential_columns_and_init_is_idempotent(tmp_path):
    p = str(tmp_path / "p.db")
    TenantStore(p); TenantStore(p)
    c = sqlite3.connect(p)
    cols = {r[1].lower() for t in ("tenants", "tenant_settings", "tenant_audit")
            for r in c.execute(f"PRAGMA table_info({t})")}
    assert not any(w in col for col in cols for w in ("password", "secret", "token", "api_key", "mpin", "totp", "pin"))
    assert "credential_ref" in cols                                    # a reference name only


def test_suspend_and_audit_trail_is_append_only_ordered(store):
    store.create_tenant("alice", "Alice")
    store.set_status("alice", "SUSPENDED", actor="admin"); store.set_status("alice", "ACTIVE", actor="admin")
    assert [a["action"] for a in store.audit_trail("alice")] == ["TENANT_CREATED", "STATUS_CHANGED", "STATUS_CHANGED"]
    with pytest.raises(TenantError):
        store.set_status("alice", "DELETED", actor="admin")


def test_platform_db_is_separate_from_production_and_not_created_on_import(monkeypatch):
    monkeypatch.delenv("CHANAKYA_PLATFORM_DB", raising=False)
    assert config.platform_db_path().endswith("/backend/data/platform.db")
    assert "chanakya.db" not in config.platform_db_path() and "market_history" not in config.platform_db_path()
    monkeypatch.setenv("CHANAKYA_PLATFORM_DB", "/tmp/x.db")
    assert config.platform_db_path() == "/tmp/x.db"
    before = os.path.exists(config.DEFAULT_PLATFORM_DB)
    for m in [m for m in sys.modules if m.startswith("app.platform")]:
        importlib.reload(sys.modules[m])
    assert os.path.exists(config.DEFAULT_PLATFORM_DB) == before        # import has no file side effects


def test_package_imports_no_broker_execution_or_network_code():
    banned = ("app.execution", "app.connectors", "broker", "requests", "websockets", "app.db", "app.autoscalp", "app.dhan_client")
    for f in glob.glob(os.path.join(PKG, "**", "*.py"), recursive=True):
        tree = ast.parse(open(f).read())
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [("." * node.level) + (node.module or "")] if isinstance(node, ast.ImportFrom) else [])
            for n in names:
                assert not any(n == b or n.startswith(b + ".") or n.lstrip(".").startswith(b) for b in banned), (f, n)


def test_stdlib_platform_is_not_shadowed_even_if_app_dir_is_on_sys_path():
    import subprocess
    app_dir = os.path.abspath(os.path.join(PKG, ".."))
    out = subprocess.run([sys.executable, "-c",
                          "import platform, numpy; print(platform.__file__); print(platform.system())"],
                         env={**os.environ, "PYTHONPATH": app_dir}, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    assert "/app/platform" not in lines[0] and lines[0].endswith("platform.py") and lines[1] == "Linux"


def test_package_still_imports_normally_as_app_platform():
    import app.platform as p
    assert p.__name__ == "app.platform"
