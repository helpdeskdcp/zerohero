"""
REGRESSION: no test / CI / backtest may open the live production database
(backend/data/chanakya.db) for writing, and the app's DB layer must be pointed
at a throwaway file during tests.

History: a manual `<move> data/chanakya.db <away> && pytest && <move back>` once
ran while the service was live -> SQLite created an empty stub -> the autoscalp
runner died on `no such table: app_settings`. This test makes that class of
mistake fail loudly in CI.

NOTE: we do NOT hash the live file before/after -- on a dev box the running
oi-dashboard.service writes to it every tick, so its bytes change with no test
involved. We assert on *what gets opened* instead (via an audit hook).
"""
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import db as DB                                   # noqa: E402

_BACKEND = Path(__file__).parents[1]
_LIVE_DB = (_BACKEND / "data" / "chanakya.db").resolve()
_SELF = Path(__file__).name


def _norm(target) -> str:
    s = str(target)
    if s.startswith("file:"):
        s = s[5:]
    return os.path.abspath(s.split("?", 1)[0])


# ---------------------------------------------------------------- redirection --

def test_db_layer_is_redirected_away_from_live_db():
    assert Path(DB.LIVE_DB_PATH).resolve() == _LIVE_DB
    assert Path(DB._resolve_db_path()).resolve() != _LIVE_DB, \
        "app.db is pointed at the LIVE db during tests!"
    assert Path(DB.DB_PATH).resolve() != _LIVE_DB
    for var in ("TEST_DATABASE_URL", "CHANAKYA_DB_PATH"):
        assert os.environ.get(var), f"{var} not set for tests"
        assert Path(os.environ[var]).resolve() != _LIVE_DB


def test_hcs_modules_resolve_to_the_test_db():
    resolved = Path(DB._resolve_db_path()).resolve()
    for modname in ("app.hcs.engine", "app.hcs.memory", "app.hcs.calibrate",
                    "app.hcs.forward_test", "app.hcs.adaptive", "app.hcs.adaptive_mc"):
        mod = __import__(modname, fromlist=["_DB"])
        assert Path(mod._DB).resolve() == resolved, f"{modname}._DB -> {mod._DB}"
        assert Path(mod._DB).resolve() != _LIVE_DB


# --------------------------------------------------------- behavioural proof --

def test_exercising_the_stack_never_opens_the_live_db_rw(tmp_path, monkeypatch):
    opened: list = []

    def audit(event, args):
        if event == "sqlite3.connect" and args:
            uri = str(args[0])
            if _norm(args[0]) == str(_LIVE_DB) and "mode=ro" not in uri and "immutable=1" not in uri:
                opened.append(uri)

    sys.addaudithook(audit)   # audit hooks cannot be removed -> keep it trivial

    test_db = tmp_path / "probe.db"
    monkeypatch.setenv("TEST_DATABASE_URL", str(test_db))
    monkeypatch.setenv("CHANAKYA_DB_PATH", str(test_db))
    import importlib
    importlib.reload(DB)
    assert Path(DB.DB_PATH).resolve() == test_db.resolve()

    DB.init_db()
    DB.set_setting("regression_probe", "value-123")
    assert DB.get_setting("regression_probe") == "value-123"

    for modname in ("app.hcs.engine", "app.hcs.forward_test", "app.hcs.calibrate"):
        m = importlib.reload(__import__(modname, fromlist=["x"]))
        for fn in ("replay", "report", "run"):
            if hasattr(m, fn):
                try:
                    getattr(m, fn)()
                except Exception:
                    pass

    try:
        from app.research_engines.order_pressure import data as OD
        OD.coverage() if hasattr(OD, "coverage") else None
    except Exception:
        pass

    assert not opened, f"live db opened read-write: {opened}"

    # the redirection actually works: the probe write is in the throwaway
    con = sqlite3.connect(str(test_db))
    row = con.execute("SELECT value FROM app_settings WHERE key='regression_probe'").fetchone()
    con.close()
    assert row and row[0] == "value-123"

    # restore the session default for later tests
    monkeypatch.undo()
    importlib.reload(DB)


# ----------------------------------------------------- moved-while-open guard --

def test_db_moved_while_open_detects_orphaned_wal(tmp_path):
    p = tmp_path / "x.db"
    assert DB.db_moved_while_open(str(p)) == []          # nothing there -> fine (fresh checkout)
    p.write_bytes(b"SQLite format 3\x00")
    (tmp_path / "x.db-wal").write_bytes(b"")
    assert DB.db_moved_while_open(str(p)) == []          # main file present -> fine
    p.unlink()                                            # <-- the `mv away` that never got `mv`d back
    assert DB.db_moved_while_open(str(p)) == ["-wal"]
    (tmp_path / "x.db-shm").write_bytes(b"")
    assert set(DB.db_moved_while_open(str(p))) == {"-wal", "-shm"}


def test_conftest_aborts_when_live_db_moved_while_open(tmp_path, monkeypatch):
    """pytest_configure raises if data/chanakya.db is gone but -wal/-shm stay,
    and stays quiet in every normal state."""
    import pytest as _pytest
    conftest = sys.modules.get("conftest") or sys.modules.get("tests.conftest")
    assert conftest is not None and hasattr(conftest, "pytest_configure")

    fake = tmp_path / "chanakya.db"
    monkeypatch.setattr(conftest, "_LIVE_DB", str(fake))

    conftest.pytest_configure(None)                       # nothing on disk -> ok (fresh checkout)
    fake.write_bytes(b"SQLite format 3\x00")
    (tmp_path / "chanakya.db-wal").write_bytes(b"")
    conftest.pytest_configure(None)                       # main file present -> ok

    fake.unlink()                                          # the move that never got undone
    with _pytest.raises(_pytest.UsageError, match="moved/renamed"):
        conftest.pytest_configure(None)


# ------------------------------------------------------------- static guard --

def test_no_test_or_script_moves_the_live_db():
    """No test/script/CI file may rename, move, delete, or truncate the live db."""
    roots = [_BACKEND / "tests", _BACKEND / "scripts", _BACKEND.parent / ".github"]
    bad = re.compile(
        r"(^\s*(mv|cp|rm|dd|install)\s+[^|;&]*\bchanakya\.db"
        r"|>\s*\S*chanakya\.db\b"                       # truncating shell redirect
        r"|os\.rename\([^)]*chanakya"
        r"|shutil\.(move|copy\w*)\([^)]*chanakya\.db"
        r"|os\.(remove|unlink|truncate)\([^)]*chanakya\.db"
        r"|Path\([^)]*chanakya\.db[^)]*\)\.(unlink|rename|replace)"
        r"|truncate\s+\S*chanakya\.db)")
    hits = []
    for root in roots:
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if not (f.is_file() and f.suffix in (".py", ".sh", ".yml", ".yaml")):
                continue
            if f.name == _SELF:            # this file documents the anti-pattern on purpose
                continue
            for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
                    continue
                if bad.search(line):
                    hits.append(f"{f.relative_to(_BACKEND.parent)}:{i}: {line.strip()}")
    assert not hits, "code that moves/deletes the live DB:\n" + "\n".join(hits)
