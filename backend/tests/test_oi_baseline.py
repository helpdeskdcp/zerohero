"""OiBaseline (lazy, per-contract) must return exactly what the old whole-symbol aggregation returned, with one indexed read per contract and none in get()."""
import random
import sqlite3

import pytest

from app.histcap.worker import OiBaseline

OLD = ("SELECT q.expiry, q.strike, q.option_type, q.oi FROM quote_snapshots q JOIN ("
       "  SELECT COALESCE(expiry,'') e, COALESCE(strike,-1) s, COALESCE(option_type,'') o, MAX(received_ts) mts FROM quote_snapshots "
       "  WHERE symbol=? AND kind IN ('OPTION','FUTURE') AND session_date_ist<? AND oi IS NOT NULL AND oi>0 "
       "  GROUP BY COALESCE(expiry,''), COALESCE(strike,-1), COALESCE(option_type,'')"
       ") m ON COALESCE(q.expiry,'')=m.e AND COALESCE(q.strike,-1)=m.s AND COALESCE(q.option_type,'')=m.o AND q.received_ts=m.mts WHERE q.symbol=? AND q.kind IN ('OPTION','FUTURE')")


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    p = str(tmp_path_factory.mktemp("oi") / "m.db"); c = sqlite3.connect(p)
    c.executescript("""CREATE TABLE quote_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, received_ts TEXT, exch_ts TEXT, symbol TEXT, kind TEXT, expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT, oi REAL);
        CREATE INDEX ix_quotes_opt ON quote_snapshots(symbol, expiry, strike, option_type, exch_ts); CREATE INDEX ix_quotes_day ON quote_snapshots(session_date_ist, symbol);""")
    rnd = random.Random(3); rows = []; days = ["2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27", "2026-10-28"]
    contracts = [("29OCT2026", float(k), ot) for k in range(24000, 24500, 50) for ot in ("CE", "PE")] + [("27NOV2026", None, None)]
    for d_i, day in enumerate(days):
        for e, k, ot in contracts:
            if e == "29OCT2026" and k == 24100 and day == "2026-10-28": continue                          # a contract with no row today (still has a baseline)
            for t in range(6):
                ts = f"{day}T0{4 + t}:{rnd.randint(10, 59)}:{rnd.randint(10, 59)}.{rnd.randint(100000, 999999)}Z"
                oi = float(rnd.randint(1000, 90000)) if t < 5 else (0.0 if (k == 24200 and day == "2026-10-27") else float(rnd.randint(1000, 90000)))        # a zero-OI close must fall back to the last row with oi > 0
                if k == 24300 and ot == "CE" and day != "2026-10-22": oi = None                          # null OI in later sessions: fall back to 22-Oct
                rows.append((ts, ts, "NIFTY", "OPTION" if k else "FUTURE", e, k, ot, day, oi))
    rows.append(("2026-10-27T10:00:00.000001Z", "2026-10-27T10:00:00Z", "NIFTY", "INDEX", None, None, None, "2026-10-27", 5.0))                  # other kinds are ignored
    rows.append(("2026-10-27T10:00:00.000001Z", "2026-10-27T10:00:00Z", "BANKNIFTY", "OPTION", "29OCT2026", 24000.0, "CE", "2026-10-27", 777.0))     # other symbols are ignored
    c.executemany("INSERT INTO quote_snapshots(received_ts, exch_ts, symbol, kind, expiry, strike, option_type, session_date_ist, oi) VALUES (?,?,?,?,?,?,?,?,?)", rows); c.commit(); c.close()
    return p, contracts


def test_same_values_as_the_old_aggregation(db):
    p, contracts = db; c = sqlite3.connect(p)
    old = {(r[0] or "", r[1], r[2]): r[3] for r in c.execute(OLD, ("NIFTY", "2026-10-28", "NIFTY"))}
    b = OiBaseline(lambda: sqlite3.connect(p), "NIFTY", today="2026-10-28"); keys = [(e, k, ot) for e, k, ot in contracts]; b.prefetch(keys)
    assert b.today == "2026-10-28" and bool(b) and len(b) == len(old)
    for k in keys: assert b.get(k) == old.get(k), k
    assert b.get(("29OCT2026", 24200.0, "CE")) != 0.0 and b.get(("29OCT2026", 24300.0, "CE")) is not None                    # zero / null OI closes fall back to the last row with oi > 0
    assert b.get(("29OCT2026", 99999.0, "CE")) is None and b.get(("none", None, None)) is None                               # never-seen contract: no baseline, no fake 0


def test_get_never_queries_and_repeat_prefetch_is_free(db):
    p, contracts = db; conn = sqlite3.connect(p); n = {"q": 0}; opened = []
    def opener(): c = sqlite3.connect(p); c.set_trace_callback(lambda s: n.__setitem__("q", n["q"] + 1)); opened.append(c); return c
    b = OiBaseline(opener, "NIFTY", today="2026-10-28"); b.prefetch(contracts[:5]); first = n["q"]; assert first == 5                              # exactly one indexed read per contract, nothing else
    for k in contracts[:5]: b.get(k)
    b.prefetch(contracts[:5]); assert n["q"] == first                                                                       # memo hit: nothing to do
    b.prefetch(contracts[:7]); assert n["q"] == first + 2


def test_the_per_contract_read_uses_the_contract_index(db):
    p, _ = db; c = sqlite3.connect(p)
    plan = " ".join(str(r[3]) for r in c.execute("EXPLAIN QUERY PLAN SELECT oi FROM quote_snapshots WHERE symbol=? AND expiry IS ? AND strike IS ? AND option_type IS ? AND kind IN ('OPTION','FUTURE') AND session_date_ist<? "
                                                 "AND oi IS NOT NULL AND oi>0 ORDER BY exch_ts DESC, id DESC LIMIT 1", ("NIFTY", "29OCT2026", 24000.0, "CE", "2026-10-28")))
    assert "ix_quotes_opt" in plan and "SCAN quote_snapshots" not in plan
    plan2 = " ".join(str(r[3]) for r in c.execute("EXPLAIN QUERY PLAN SELECT oi FROM quote_snapshots WHERE symbol=? AND expiry IS ? AND strike IS ? AND option_type IS ? AND kind IN ('OPTION','FUTURE') AND session_date_ist<? "
                                                  "AND exch_ts<? AND oi IS NOT NULL AND oi>0 ORDER BY exch_ts DESC, id DESC LIMIT 1", ("NIFTY", "29OCT2026", 24000.0, "CE", "2026-10-28", "2026-10-27T18:30")))
    assert "exch_ts<?" in plan2 and "ix_quotes_opt" in plan2                                           # the bound is part of the index range: today's rows are never read


def test_no_rows_means_falsy_and_no_baseline(tmp_path):
    p = str(tmp_path / "e.db"); c = sqlite3.connect(p); c.execute("CREATE TABLE quote_snapshots (id INTEGER PRIMARY KEY, received_ts TEXT, exch_ts TEXT, symbol TEXT, kind TEXT, expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT, oi REAL)"); c.commit(); c.close()
    b = OiBaseline(lambda: sqlite3.connect(p), "NIFTY", today="2026-10-28"); b.prefetch([("29OCT2026", 24000.0, "CE")]); assert not b and b.get(("29OCT2026", 24000.0, "CE")) is None


def test_a_contract_with_thousands_of_rows_today_costs_the_same_as_one_with_none(tmp_path):
    def build(path, today_rows):
        c = sqlite3.connect(path)
        c.executescript("""CREATE TABLE quote_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, received_ts TEXT, exch_ts TEXT, symbol TEXT, kind TEXT, expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT, oi REAL);
            CREATE INDEX ix_quotes_opt ON quote_snapshots(symbol, expiry, strike, option_type, exch_ts); CREATE INDEX ix_quotes_day ON quote_snapshots(session_date_ist, symbol);""")
        rows = [("2026-10-27T09:59:59.5Z", "2026-10-27T09:59:59Z", "NATURALGAS", "OPTION", "20NOV2026", 290.0, "PE", "2026-10-27", 4200.0)]               # the previous session's close
        rows += [(f"2026-10-28T{4 + i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}.1Z", f"2026-10-28T{4 + i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}Z", "NATURALGAS", "OPTION", "20NOV2026", 290.0, "PE", "2026-10-28", 4300.0) for i in range(today_rows)]
        rows += [("2026-10-28T04:00:00.1Z", "2026-10-28T04:00:00Z", "NATURALGAS", "OPTION", "20NOV2026", 300.0, "CE", "2026-10-28", 1.0)]                  # keeps "today" defined when today_rows == 0
        c.executemany("INSERT INTO quote_snapshots(received_ts, exch_ts, symbol, kind, expiry, strike, option_type, session_date_ist, oi) VALUES (?,?,?,?,?,?,?,?,?)", rows); c.commit(); c.close()
    def steps(path):
        n = {"k": 0}
        def opener(): k = sqlite3.connect(path); k.set_progress_handler(lambda: n.__setitem__("k", n["k"] + 1) or 0, 50); return k
        b = OiBaseline(opener, "NATURALGAS", today="2026-10-28"); b.prefetch([("20NOV2026", 290.0, "PE")]); assert b.get(("20NOV2026", 290.0, "PE")) == 4200.0; return n["k"]
    build(str(tmp_path / "few.db"), 5); build(str(tmp_path / "many.db"), 9000)
    few, many = steps(str(tmp_path / "few.db")), steps(str(tmp_path / "many.db"))
    assert many <= few * 2 + 5, (few, many)                                                       # 9,000 of today's rows are not walked


def test_rows_without_an_exchange_stamp_are_still_found_when_nothing_stamped_exists(tmp_path):
    p = str(tmp_path / "nul.db"); c = sqlite3.connect(p)
    c.executescript("CREATE TABLE quote_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, received_ts TEXT, exch_ts TEXT, symbol TEXT, kind TEXT, expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT, oi REAL); CREATE INDEX ix_quotes_opt ON quote_snapshots(symbol, expiry, strike, option_type, exch_ts);")
    c.execute("INSERT INTO quote_snapshots(received_ts, exch_ts, symbol, kind, expiry, strike, option_type, session_date_ist, oi) VALUES ('2026-10-27T10:00:00.1Z', NULL, 'NIFTY', 'OPTION', '03NOV2026', 22500.0, 'PE', '2026-10-27', 555.0)")
    c.execute("INSERT INTO quote_snapshots(received_ts, exch_ts, symbol, kind, expiry, strike, option_type, session_date_ist, oi) VALUES ('2026-10-28T10:00:00.1Z', '2026-10-28T10:00:00Z', 'NIFTY', 'OPTION', '03NOV2026', 22500.0, 'PE', '2026-10-28', 600.0)"); c.commit(); c.close()
    b = OiBaseline(lambda: sqlite3.connect(p), "NIFTY", today="2026-10-28"); b.prefetch([("03NOV2026", 22500.0, "PE")]); assert b.get(("03NOV2026", 22500.0, "PE")) == 555.0
