"""Shared test fixtures. Each test module gets a fresh throwaway SQLite file."""
import os
import sys
import time
import tempfile

import pytest

# make `import app...` work when pytest is run from backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
