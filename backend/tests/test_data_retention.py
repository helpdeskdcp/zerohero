import importlib.util, os, sqlite3
from datetime import datetime, timedelta, timezone

spec = importlib.util.spec_from_file_location("data_retention", os.path.join(os.path.dirname(__file__), "..", "scripts", "data_retention.py"))
DR = importlib.util.module_from_spec(spec); spec.loader.exec_module(DR)
IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=IST)


def _mk(tmp_path):
    l2 = sqlite3.connect(tmp_path / "l2_capture.db")
    l2.execute("CREATE TABLE raw_frames (id INTEGER PRIMARY KEY, received_ts TEXT)"); l2.execute("CREATE TABLE snapquote_ticks (id INTEGER PRIMARY KEY, session_date_ist TEXT)")
    for i in range(40):
        d = (NOW - timedelta(days=39 - i)).strftime("%Y-%m-%d")
        l2.execute("INSERT INTO raw_frames (received_ts) VALUES (?)", (d + "T05:00:00Z",)); l2.execute("INSERT INTO snapquote_ticks (session_date_ist) VALUES (?)", (d,))
    l2.commit(); l2.close()
    mh = sqlite3.connect(tmp_path / "market_history.db")
    for t in ("quote_snapshots", "option_greeks"): mh.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, session_date_ist TEXT)")
    for i in range(100):
        d = (NOW - timedelta(days=99 - i)).strftime("%Y-%m-%d")
        for t in ("quote_snapshots", "option_greeks"): mh.execute(f"INSERT INTO {t} (session_date_ist) VALUES (?)", (d,))
    mh.commit(); mh.close()


def test_dry_run_deletes_nothing_and_apply_keeps_window_and_contest(tmp_path, monkeypatch):
    _mk(tmp_path); monkeypatch.setattr(DR, "PAUSE", 0); monkeypatch.setattr(DR, "BATCH", 3); monkeypatch.setattr(DR, "contest_start", lambda: "2026-10-20")
    out = DR.run(False, data_dir=str(tmp_path), now=NOW, log=lambda *_: None)
    assert {(r["table"]): r["rows"] for r in out} == {"raw_frames": 32, "snapquote_ticks": 18, "quote_snapshots": 39, "option_greeks": 39}
    assert sqlite3.connect(tmp_path / "l2_capture.db").execute("SELECT COUNT(*) FROM raw_frames").fetchone()[0] == 40
    monkeypatch.setattr(DR, "market_open", lambda: False)
    DR.run(True, data_dir=str(tmp_path), now=NOW, log=lambda *_: None)
    l2 = sqlite3.connect(tmp_path / "l2_capture.db"); mh = sqlite3.connect(tmp_path / "market_history.db")
    assert l2.execute("SELECT COUNT(*), MIN(received_ts) FROM raw_frames").fetchone() == (8, (NOW - timedelta(days=7)).strftime("%Y-%m-%d") + "T05:00:00Z")
    assert l2.execute("SELECT COUNT(*) FROM snapquote_ticks").fetchone()[0] == 22
    assert mh.execute("SELECT COUNT(*) FROM quote_snapshots").fetchone()[0] == 61 and mh.execute("SELECT MIN(session_date_ist) FROM option_greeks").fetchone()[0] == (NOW - timedelta(days=60)).strftime("%Y-%m-%d")


def test_contest_sessions_are_never_deleted(tmp_path, monkeypatch):
    _mk(tmp_path); monkeypatch.setattr(DR, "PAUSE", 0); monkeypatch.setattr(DR, "market_open", lambda: False); monkeypatch.setattr(DR, "contest_start", lambda: "2026-08-20")
    DR.run(True, data_dir=str(tmp_path), now=NOW, log=lambda *_: None)
    mh = sqlite3.connect(tmp_path / "market_history.db")
    assert mh.execute("SELECT MIN(session_date_ist) FROM quote_snapshots").fetchone()[0] == "2026-08-20"         # older than 60 days, but inside the contest window


def test_refuses_while_market_is_trading(tmp_path, monkeypatch):
    _mk(tmp_path); monkeypatch.setattr(DR, "market_open", lambda: True)
    assert DR.run(True, data_dir=str(tmp_path), now=NOW, log=lambda *_: None) == []
    assert sqlite3.connect(tmp_path / "l2_capture.db").execute("SELECT COUNT(*) FROM raw_frames").fetchone()[0] == 40


class FakeArch:
    def __init__(self, used=0, ok=True): self.used, self.ok, self.put_names = used, ok, []
    def used_bytes(self): return self.used
    def put(self, local, rel):
        if self.ok and len(self.put_names) < getattr(self, "limit", 10 ** 9): self.put_names.append((rel, __import__("gzip").open(local, "rt").read())); return True
        return False


def _tick_db(tmp_path):
    c = sqlite3.connect(tmp_path / "l2_capture.db")
    c.execute("CREATE TABLE raw_frames (id INTEGER PRIMARY KEY, received_ts TEXT)"); c.execute("CREATE TABLE snapquote_ticks (id INTEGER PRIMARY KEY, session_date_ist TEXT, x TEXT, y REAL)")
    for i in range(40):
        d = (NOW - timedelta(days=39 - i)).strftime("%Y-%m-%d")
        for k in range(3): c.execute("INSERT INTO snapquote_ticks (session_date_ist, x, y) VALUES (?,?,?)", (d, None if k == 0 else f"v{k}", 1.5 * k))
    c.commit(); c.close()


def test_archive_then_delete_only_archived_days(tmp_path, monkeypatch):
    _tick_db(tmp_path); monkeypatch.setattr(DR, "PAUSE", 0); monkeypatch.setattr(DR, "market_open", lambda: False); monkeypatch.setattr(DR, "contest_start", lambda: None)
    a = FakeArch(); a.limit = 5                                                       # upload fails after 5 days -> only those 5 days may be deleted
    out = DR.run(True, data_dir=str(tmp_path), now=NOW, log=lambda *_: None, archiver=a)
    t = [r for r in out if r["table"] == "snapquote_ticks"][0]; assert t["archive"]["stopped"] == "UPLOAD_FAILED" and t["rows"] == 15 and len(a.put_names) == 5
    c = sqlite3.connect(tmp_path / "l2_capture.db"); assert c.execute("SELECT COUNT(*) FROM snapquote_ticks").fetchone()[0] == 120 - 15
    rel, body = a.put_names[0]; assert rel.startswith("l2_capture/snapquote_ticks/") and rel.endswith(".csv.gz") and "\\N" in body and "v1" in body and body.count("\n") == 4        # header + 3 rows


def test_quota_guard_falls_back_to_delete_only(tmp_path, monkeypatch):
    _tick_db(tmp_path); monkeypatch.setattr(DR, "PAUSE", 0); monkeypatch.setattr(DR, "market_open", lambda: False); monkeypatch.setattr(DR, "contest_start", lambda: None)
    sent = []; monkeypatch.setattr(DR, "alert", lambda t: sent.append(t))
    a = FakeArch(used=DR.QUOTA_GUARD)
    DR.run(True, data_dir=str(tmp_path), now=NOW, log=lambda *_: None, archiver=a)
    assert a.put_names == [] and sent and "WITHOUT archive" in sent[0]
    assert sqlite3.connect(tmp_path / "l2_capture.db").execute("SELECT COUNT(*) FROM snapquote_ticks").fetchone()[0] == 22 * 3         # still trimmed to 21+1 days


def test_dry_run_with_archiver_uploads_nothing(tmp_path, monkeypatch):
    _tick_db(tmp_path); monkeypatch.setattr(DR, "contest_start", lambda: None); a = FakeArch()
    out = DR.run(False, data_dir=str(tmp_path), now=NOW, log=lambda *_: None, archiver=a)
    assert a.put_names == [] and [r for r in out if r["table"] == "snapquote_ticks"][0]["archive"]["days"] == 18
