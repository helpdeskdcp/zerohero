"""
Regression tests for the HTTP-Basic auth gate's credential handling
(app/main.py). Added 2026-09-19 per ZEROHERO_FULL_AUDIT_2026-09-19.md's
HIGH finding: the app used to fall back to a hardcoded, publicly-known
default credential (admin/admin@1234) whenever CHANAKYA_ADMIN_USERNAME/
CHANAKYA_ADMIN_PASSWORD were unset. These tests prove:

  1. that public default can never authenticate again, regardless of how
     the deployment is configured;
  2. an unconfigured deployment fails CLOSED (denies everything), not open;
  3. a correctly-configured deployment still authenticates exactly as
     before (auth architecture/behaviour unchanged for the intended case).

Tests exercise `_basic_ok` directly (module import only -- no TestClient /
app startup, matching this repo's existing convention of not spinning up
the FastAPI lifecycle for unit-level checks).
"""
import base64

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app import main


def _basic_header(user: str, password: str) -> str:
    raw = f"{user}:{password}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def test_old_public_default_credential_never_authenticates(monkeypatch):
    """The exact credential that used to be the hardcoded fallback --
    published in this repo's git history -- must never work again, no
    matter what the deployment's real configured credential is."""
    monkeypatch.setattr(main, "ADMIN_USERNAME", "realoperator")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "a-real-rotated-secret")
    assert main._basic_ok(_basic_header("admin", "admin@1234")) is False


def test_unconfigured_deployment_fails_closed(monkeypatch):
    """If CHANAKYA_ADMIN_USERNAME/PASSWORD are unset (empty), Basic-Auth
    must deny every attempt -- including one that happens to send empty
    credentials -- never silently accept."""
    monkeypatch.setattr(main, "ADMIN_USERNAME", "")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "")
    assert main._basic_ok(_basic_header("admin", "admin@1234")) is False
    assert main._basic_ok(_basic_header("", "")) is False
    assert main._basic_ok("Basic " + base64.b64encode(b":").decode()) is False


def test_partially_configured_deployment_fails_closed(monkeypatch):
    """Only one of the two vars set is still an unconfigured deployment --
    must deny, not partially trust."""
    monkeypatch.setattr(main, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "")
    assert main._basic_ok(_basic_header("admin", "")) is False

    monkeypatch.setattr(main, "ADMIN_USERNAME", "")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "something")
    assert main._basic_ok(_basic_header("", "something")) is False


def test_correctly_configured_deployment_still_authenticates(monkeypatch):
    """Preserve existing behaviour: a real, non-default credential
    configured via the normal mechanism (env vars) must still work."""
    monkeypatch.setattr(main, "ADMIN_USERNAME", "opuser")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "SomeStrongRotatedValue123")
    assert main._basic_ok(_basic_header("opuser", "SomeStrongRotatedValue123")) is True


def test_correctly_configured_deployment_rejects_wrong_password(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_USERNAME", "opuser")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "SomeStrongRotatedValue123")
    assert main._basic_ok(_basic_header("opuser", "wrong")) is False


def test_no_hardcoded_default_credential_in_source():
    """Static guard: the well-known default string must never reappear as
    a literal fallback in app/main.py again."""
    src = (main.__file__ and __import__("pathlib").Path(main.__file__).read_text()) or ""
    assert "admin@1234" not in src


# ---------------------------------------------------------------------------
# _AuthGateMiddleware -- ASGI-level behavior (2026-09-28 rewrite: replaced
# @app.middleware("http")/BaseHTTPMiddleware, which was confirmed live to
# silently drop a route's BackgroundTasks, with a plain ASGI middleware that
# never touches the response object at all). Tested against a tiny throwaway
# Starlette app wrapping the real _AuthGateMiddleware class -- never boots
# the real app (no DB/broker dependencies pulled in for a unit test).
# ---------------------------------------------------------------------------
def _dummy_client(monkeypatch, *, username="opuser", password="strongpw", api_token=""):
    monkeypatch.setattr(main, "ADMIN_USERNAME", username)
    monkeypatch.setattr(main, "ADMIN_PASSWORD", password)
    monkeypatch.setattr(main, "API_TOKEN", api_token)

    async def ok(request):
        return JSONResponse({"ok": True})

    inner = Starlette(routes=[Route("/api/health", ok), Route("/api/whoami", ok)])
    wrapped = main._AuthGateMiddleware(inner)
    return TestClient(wrapped)


def test_health_is_open_without_any_credential(monkeypatch):
    client = _dummy_client(monkeypatch)
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_protected_route_denies_with_no_credential(monkeypatch):
    client = _dummy_client(monkeypatch)
    resp = client.get("/api/whoami")
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate", "").lower().startswith("basic")


def test_protected_route_allows_correct_basic_auth(monkeypatch):
    client = _dummy_client(monkeypatch)
    resp = client.get("/api/whoami", auth=("opuser", "strongpw"))
    assert resp.status_code == 200


def test_protected_route_rejects_wrong_basic_auth(monkeypatch):
    client = _dummy_client(monkeypatch)
    resp = client.get("/api/whoami", auth=("opuser", "wrongpw"))
    assert resp.status_code == 401


def test_protected_route_allows_correct_bearer_token(monkeypatch):
    client = _dummy_client(monkeypatch, api_token="secret-tok")
    resp = client.get("/api/whoami", headers={"Authorization": "Bearer secret-tok"})
    assert resp.status_code == 200


def test_protected_route_rejects_wrong_bearer_token(monkeypatch):
    client = _dummy_client(monkeypatch, api_token="secret-tok")
    resp = client.get("/api/whoami", headers={"Authorization": "Bearer wrong-tok"})
    assert resp.status_code == 401


def test_non_http_scope_passes_through_untouched(monkeypatch):
    """websocket/lifespan scopes must never hit the auth check -- the old
    @app.middleware("http") decorator only ever applied to http scopes by
    construction; the rewrite must preserve that exactly, or /ws breaks."""
    import asyncio

    monkeypatch.setattr(main, "ADMIN_USERNAME", "opuser")
    monkeypatch.setattr(main, "ADMIN_PASSWORD", "strongpw")
    monkeypatch.setattr(main, "API_TOKEN", "")

    calls = []

    async def inner_app(scope, receive, send):
        calls.append(scope["type"])

    mw = main._AuthGateMiddleware(inner_app)
    asyncio.run(mw({"type": "websocket"}, None, None))
    assert calls == ["websocket"]
