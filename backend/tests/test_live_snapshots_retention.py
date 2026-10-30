import gzip, json, os, sqlite3, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import live_snapshots_retention as L

IST = timezone(timedelta(hours=5, minutes=30)); NOW = datetime(2026, 10, 30, 12, 0, tzinfo=IST)


def _db(path):
    c = sqlite3.connect(path); c.execute("CREATE TABLE live_market_snapshots (id INTEGER PRIMARY KEY, ts TEXT, session_date TEXT, symbol TEXT, decision TEXT, chain_json TEXT)")
    for i, d in enumerate(("2026-10-10", "2026-10-10", "2026-10-20", "2026-10-29", "2026-10-30")):
        c.execute("INSERT INTO live_market_snapshots (ts, session_date, symbol, decision, chain_json) VALUES (?,?,?,?,?)", (d + "T04:00:00Z", d, "NIFTY", "NO_TRADE", json.dumps([{"strike": i}])))
    c.execute("INSERT INTO live_market_snapshots (ts, session_date, symbol, decision, chain_json) VALUES ('2026-10-11T04:00:00Z','2026-10-11','NIFTY','WATCH',NULL)")
    c.commit(); c.close()


def test_dry_run_counts_only(tmp_path):
    db = str(tmp_path / "c.db"); _db(db)
    r = L.run(db, 7, False, False, str(tmp_path / "a"), now=NOW, log=lambda *_: None)
    assert r["rows"] == 3 and r["days"] == 2 and r["nulled"] == 0 and sqlite3.connect(db).execute("SELECT COUNT(*) FROM live_market_snapshots WHERE chain_json IS NOT NULL").fetchone()[0] == 5


def test_old_chains_are_archived_then_cleared_and_the_rows_and_recent_chains_stay(tmp_path):
    db = str(tmp_path / "c.db"); _db(db)
    r = L.run(db, 7, True, False, str(tmp_path / "a"), now=NOW, log=lambda *_: None); c = sqlite3.connect(db)
    assert r["archived"] == r["nulled"] == 3
    assert c.execute("SELECT COUNT(*) FROM live_market_snapshots").fetchone()[0] == 6                                      # no row deleted
    assert c.execute("SELECT COUNT(*) FROM live_market_snapshots WHERE chain_json IS NOT NULL").fetchone()[0] == 2         # the two recent (29 / 30-Oct) chains stay
    assert c.execute("SELECT decision FROM live_market_snapshots WHERE session_date='2026-10-10'").fetchall() == [("NO_TRADE",), ("NO_TRADE",)]
    rows = [json.loads(l) for f in os.listdir(tmp_path / "a") for l in gzip.open(tmp_path / "a" / f, "rt")]
    assert len(rows) == 3 and all("chain_json" in x for x in rows)
