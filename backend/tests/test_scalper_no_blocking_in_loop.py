"""Regression: scalper._manage must not call the blocking _wrong_side (sync HTTP candle fetch) directly on the event loop.
Found 2026-10-13 via the platform stall watchdog (4.9s and 10.8s loop stalls, 33s feed gap at 09:32)."""
import ast
import os

SRC = os.path.join(os.path.dirname(__file__), "..", "app", "scalper.py")


def test_wrong_side_only_called_via_to_thread():
    tree = ast.parse(open(SRC).read())
    bad = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef):
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "_wrong_side":
                    bad.append((fn.name, n.lineno))        # a direct call inside an async def blocks the loop
    assert not bad, bad


def test_angel_ws_credential_provider_not_called_on_loop():
    src = os.path.join(os.path.dirname(__file__), "..", "app", "connectors", "angel_ws.py")
    tree = ast.parse(open(src).read())
    bad = [(fn.name, n.lineno) for fn in ast.walk(tree) if isinstance(fn, ast.AsyncFunctionDef)
           for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "_cred_provider"]
    assert not bad, bad
