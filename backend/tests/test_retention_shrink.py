import importlib.util, os, sqlite3, tempfile
spec = importlib.util.spec_from_file_location("data_retention", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "data_retention.py"))
DR = importlib.util.module_from_spec(spec); spec.loader.exec_module(DR)
def _db(av):
    p = os.path.join(tempfile.mkdtemp(), "t.db"); c = sqlite3.connect(p); c.execute(f"PRAGMA auto_vacuum={av}"); c.execute("VACUUM"); c.execute("create table t(id integer primary key, x blob)")
    c.executemany("insert into t(x) values (?)", [(os.urandom(2000),) for _ in range(6000)]); c.commit(); return p, c
def test_shrink_returns_deleted_pages_on_incremental_files():
    p, c = _db(2); big = os.path.getsize(p); c.execute("delete from t where id <= 5000"); c.commit()
    got = DR.shrink(c, log=lambda *_: None, keep_free_bytes=0, step_pages=500); c.execute("pragma wal_checkpoint(TRUNCATE)")
    assert got > 0 and os.path.getsize(p) < big * 0.4 and c.execute("select count(*) from t").fetchone()[0] == 1000
def test_shrink_does_nothing_on_a_normal_file():
    p, c = _db(0); c.execute("delete from t where id <= 5000"); c.commit(); size = os.path.getsize(p)
    assert DR.shrink(c, log=lambda *_: None) == 0 and os.path.getsize(p) == size
