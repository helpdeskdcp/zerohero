import gzip, os, sqlite3, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import platform_candles_retention as P


def _db(path, now):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE candles (instrument TEXT, tf_sec INT, start_ts INT, provider TEXT, o REAL, h REAL, l REAL, c REAL, tick_count INT, volume REAL, volume_complete INT, oi REAL, oi_change REAL, first_ts REAL, last_ts REAL)")
    rows = []
    for d in (10, 5, 1):                                                              # days ago
        for tf in (1, 5, 60):
            for k in range(4): rows.append(("X", tf, int(now - d * 86400) + k * tf, "p", 1, 2, 0.5, 1.5, 3, 10, 1, None, None, 0, 0))
    c.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows); c.commit(); c.close()


def test_dry_run_changes_nothing(tmp_path):
    now = time.time(); db = str(tmp_path / "t.db"); _db(db, now)
    r = P.run(db, 3, False, False, str(tmp_path / "a"), now=now, log=lambda *_: None)
    assert r["eligible_rows"] == 16 and r["deleted"] == 0 and sqlite3.connect(db).execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 36


def test_old_short_candles_are_archived_then_deleted_and_the_60s_candles_stay(tmp_path):
    now = time.time(); db = str(tmp_path / "t.db"); _db(db, now)
    r = P.run(db, 3, True, False, str(tmp_path / "a"), now=now, log=lambda *_: None)
    c = sqlite3.connect(db)
    assert r["archived"] == r["deleted"] == 16
    assert c.execute("SELECT COUNT(*) FROM candles WHERE tf_sec=60").fetchone()[0] == 12                         # untouched: the only timeframe anything reads
    assert c.execute("SELECT COUNT(*) FROM candles WHERE tf_sec IN (1,5) AND start_ts>=?", (now - 3 * 86400,)).fetchone()[0] == 8      # the recent (1 day old) short candles stay: 2 timeframes x 4
    files = os.listdir(tmp_path / "a"); assert files and all(f.endswith(".csv.gz") for f in files)
    n = sum(len(gzip.open(tmp_path / "a" / f, "rt").read().strip().split("\n")) - 1 for f in files); assert n == 16   # every deleted row is in an archive file
