"""Shared test fixtures. Each test module gets a fresh throwaway SQLite file."""
import os
import sys
import time
import tempfile

import pytest

# make `import app...` work when pytest is run from backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# HARD GUARD: never let a test touch the live data/chanakya.db. Point the DB at
# a session temp file BEFORE app.db is first imported, via BOTH env vars the
# resolver honours (TEST_DATABASE_URL wins). Tests that need a clean schema use
# the `fresh_db` fixture (its own tmp file); this ensures a stray
# main.autoscalp.set_config / api_* call in a test without fresh_db writes to a
# throwaway, never to the running service's DB.
_SESSION_DB = os.path.join(tempfile.mkdtemp(prefix="chanakya-test-"), "session.db")
os.environ["TEST_DATABASE_URL"] = _SESSION_DB
os.environ["CHANAKYA_DB_PATH"] = _SESSION_DB

# The real production DB. A hash-before/after check is NOT reliable here: on a
# dev box the live oi-dashboard.service holds this file open and writes to it
# every tick, so its bytes change constantly with no test involved. Instead we
# forbid any test from *opening* it for writing, via an audit hook.
_LIVE_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "chanakya.db"))
_LIVE_DB_OPENED_RW: list = []


def _stat_or_none(path):
    try:
        st = os.stat(path)
        return (st.st_ino, st.st_size)
    except OSError:
        return None


# Snapshot the live DB the moment pytest starts importing conftest -- long before
# any test runs. The session-end fixture and pytest_configure below use it to
# catch the classic "someone ran `mv data/chanakya.db /tmp && pytest` in their
# shell and the `&& mv back` never happened" mistake, which no scan of committed
# code can see.
_LIVE_DB_AT_START = _stat_or_none(_LIVE_DB)


def pytest_configure(config):
    """Abort the whole run if the live DB looks like it was moved out from under
    an open connection (main file gone but its -wal / -shm siblings remain)."""
    from app.db import db_moved_while_open
    orph = db_moved_while_open(_LIVE_DB)
    if orph:
        raise pytest.UsageError(
            f"live DB {_LIVE_DB} is missing but {', '.join(orph)} remain -- it was "
            f"almost certainly moved/renamed while the service held it open. "
            f"Restore it before running tests (do NOT let the suite or the service "
            f"recreate an empty stub). Tests never need the live DB: they use "
            f"TEST_DATABASE_URL automatically."
        )


def _norm_sqlite_target(arg) -> str:
    s = str(arg)
    if s.startswith("file:"):
        s = s[5:]
    s = s.split("?", 1)[0]
    return os.path.abspath(s)


def _sqlite_audit(event, args):
    # args[0] is the database path/URI for sqlite3.connect
    if event == "sqlite3.connect" and args:
        uri = str(args[0])
        if _norm_sqlite_target(args[0]) == _LIVE_DB and "mode=ro" not in uri and "immutable=1" not in uri:
            _LIVE_DB_OPENED_RW.append(uri)


sys.addaudithook(_sqlite_audit)


@pytest.fixture(scope="session", autouse=True)
def _live_db_never_opened_rw():
    """Session guard: no test may open the live chanakya.db read-write, and the
    live file must not vanish or be truncated during the run (catches a manual
    `mv ... && pytest` where the move-back never happened, or a test that
    unlinked/zeroed it)."""
    start = len(_LIVE_DB_OPENED_RW)
    yield
    new = _LIVE_DB_OPENED_RW[start:]
    assert not new, f"a test opened the LIVE db read-write: {new}"

    if _LIVE_DB_AT_START is not None:
        after = _stat_or_none(_LIVE_DB)
        assert after is not None, (
            f"live DB {_LIVE_DB} DISAPPEARED during the test run (it existed at "
            f"start, inode {_LIVE_DB_AT_START[0]}). A test or the shell moved/"
            f"deleted it -- restore it from backup.")
        assert after[0] == _LIVE_DB_AT_START[0], (
            f"live DB {_LIVE_DB} was replaced during the run "
            f"(inode {_LIVE_DB_AT_START[0]} -> {after[0]}) -- something recreated it.")
        # the running service only ever grows / checkpoints it; a big shrink means
        # truncation.
        assert after[1] >= _LIVE_DB_AT_START[1] * 0.5, (
            f"live DB {_LIVE_DB} shrank from {_LIVE_DB_AT_START[1]} to {after[1]} "
            f"bytes during the run -- it was truncated.")

# HARD GUARD #2: a test must never hit the real Telegram. run_pipeline /
# run_scalp_pipeline call telegram.notify_signal for real. Strip the creds AND
# (below, via the autouse fixture) stub the HTTP sender.
for _k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_SIGNALS_CHANNEL_ID"):
    os.environ.pop(_k, None)


@pytest.fixture(autouse=True)
def _no_real_telegram(monkeypatch):
    """Every test: the Telegram HTTP call is a no-op. Belt-and-braces with the
    env strip above -- nothing a test does can reach api.telegram.org."""
    try:
        from app.connectors import telegram
        monkeypatch.setattr(telegram, "_send",
                            lambda text, chat_id: {"ok": False, "reason": "TEST_STUB"})
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _fresh_telegram_dispatcher():
    """app.telegram_dispatcher's canonical dispatcher is a process-lifetime
    singleton by design (its whole point is a registry SHARED across every
    live engine, for cross-engine agreement/conflict detection) -- but that
    means, left alone, its in-memory recent-signal registry would leak
    between unrelated tests across different test files/order, e.g. a
    "NIFTY BUY_CE" from one test making an unrelated later test's "NIFTY"
    signal spuriously look CONFIRMED or in CONFLICT. Reset it before every
    test so each test only ever sees its own dispatches."""
    import app.telegram_dispatcher as _td
    _td._singleton = None
    yield
    _td._singleton = None


@pytest.fixture()
def fresh_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("TEST_DATABASE_URL", path)
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
