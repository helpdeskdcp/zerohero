"""Shared test fixtures. Each test module gets a fresh throwaway SQLite file."""
import os
import sys
import time
import tempfile

import pytest

# make `import app...` work when pytest is run from backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# HARD GUARD: never let a test touch the live data/chanakya.db. Point the DB at
# a session temp file BEFORE app.db is first imported. Tests that need a clean
# schema still use the `fresh_db` fixture (which swaps in its own tmp file);
# this only ensures a stray main.autoscalp.set_config / api_* call in a test
# without fresh_db writes to a throwaway, not to the running service's DB.
_SESSION_DB = os.path.join(tempfile.mkdtemp(prefix="chanakya-test-"), "session.db")
os.environ["CHANAKYA_DB_PATH"] = _SESSION_DB


@pytest.fixture()
def fresh_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("CHANAKYA_DB_PATH", path)
    # re-import db so module-level DB_PATH picks up the env
    import importlib
    from app import db as _db
    importlib.reload(_db)
    _db.init_db()
    yield _db
    for ext in ("", "-wal", "-shm"):
        try:
            os.remove(path + ext)
        except OSError:
            pass


def candles(prices, start=None, step=60, vol=1000):
    """[t,o,h,l,c,v] rows from a close-price list (o≈prev close, small wick)."""
    start = start or (int(time.time()) - len(prices) * step)
    out = []
    prev = prices[0]
    for i, c in enumerate(prices):
        o = prev
        hi = max(o, c) + 0.05
        lo = min(o, c) - 0.05
        out.append([start + i * step, round(o, 2), round(hi, 2), round(lo, 2), round(c, 2), vol])
        prev = c
    return out
