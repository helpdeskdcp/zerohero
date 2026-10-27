"""scripts/stocks_1m_pull.py --extend-to: appends sessions after each stored symbol's last one, idempotent, no network (fakes only)."""
import importlib.util, os, sqlite3, sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = timezone(timedelta(hours=5, minutes=30))


def _load():
    spec = importlib.util.spec_from_file_location("stocks_1m_pull_t", os.path.join(HERE, "scripts", "stocks_1m_pull.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


class _Resp:
    status_code = 200
    def __init__(self, candles): self._c = candles
    def json(self): return {"data": {"candles": self._c}}


def test_extend_appends_new_session_idempotently(tmp_path, monkeypatch):
    m = _load(); db = str(tmp_path / "s.db"); monkeypatch.setattr(m, "DB", db)
    c = sqlite3.connect(db); c.execute("CREATE TABLE bars (sym TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY (sym, ts)) WITHOUT ROWID"); c.execute("CREATE TABLE done (sym TEXT, ym TEXT, n INTEGER, PRIMARY KEY (sym, ym))")
    t23 = int(datetime(2026, 10, 23, 15, 29, tzinfo=IST).timestamp())
    c.execute("INSERT INTO bars VALUES ('AAA',?,1,2,0.5,1.5,10)", (t23,)); c.execute("INSERT INTO done VALUES ('AAA','2026-10',1)"); c.execute("INSERT INTO done VALUES ('ZZZ','2026-10',1)"); c.commit(); c.close()
    monkeypatch.setattr(m.S, "_master", lambda refresh=False: [{"trading_symbol": "AAA", "segment": "NSE_EQ", "instrument_type": "EQ", "instrument_key": "NSE_EQ|AAA"}])
    calls = []
    def fake(path):
        calls.append(path)
        return _Resp([["2026-10-23T15:29:00+05:30", 1, 2, 0.5, 1.5, 10], ["2026-10-26T09:15:00+05:30", 3, 4, 2.5, 3.5, 20], ["2026-10-26T09:16:00+05:30", 3.5, 4, 3, 3.8, None]])
    monkeypatch.setattr(m.U, "_authed_get", fake)
    m.extend("2026-10-26"); m.extend("2026-10-26")                                                       # twice: no duplicate rows
    r = sqlite3.connect(db).execute("SELECT count(*), min(ts), max(ts), sum(v) FROM bars WHERE sym='AAA'").fetchone()
    assert r[0] == 3 and r[2] == int(datetime(2026, 10, 26, 9, 16, tzinfo=IST).timestamp()) and r[3] == 30                      # None volume stored as 0
    assert len(calls) == 2 and "/minutes/1/2026-10-26/2026-10-23" in calls[0] and "/minutes/1/2026-10-26/2026-10-26" in calls[1]      # 2nd run starts from the new last day; ZZZ (not in the master) is skipped
