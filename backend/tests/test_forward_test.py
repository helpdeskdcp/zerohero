import json
import os

import pytest

from app.forward_test import ledger as L


def mk(i, e, x, u="NIFTY", day="2026-10-21"):
    return {"trade_id": f"t{i}", "opened_ts": f"{day}T04:00:00+00:00", "closed_ts": f"{day}T05:00:00+00:00", "underlying": u, "entry": e, "exit_price": x}


LOT = lambda u: {"NIFTY": 65, "NATURALGAS": 1250, "CRUDEOIL": 100}.get(u)


def test_chain_detects_tamper_delete_and_reorder(tmp_path):
    p = str(tmp_path / "l.jsonl")
    for i in range(4): L.append(p, {"type": "DAILY", "i": i})
    assert L.verify_chain(p) == (True, 4, None)
    lines = open(p).read().splitlines()
    t = json.loads(lines[1]); t["i"] = 99; bad = lines[:1] + [json.dumps(t, sort_keys=True)] + lines[2:]
    open(p, "w").write("\n".join(bad) + "\n"); ok, n, err = L.verify_chain(p); assert not ok and "line 2" in err
    open(p, "w").write("\n".join(lines[:1] + lines[2:]) + "\n"); assert not L.verify_chain(p)[0]                                    # deletion
    open(p, "w").write("\n".join([lines[1], lines[0]] + lines[2:]) + "\n"); assert not L.verify_chain(p)[0]                         # reorder


def test_modelled_net_and_skips():
    r = L.modelled_net_pct(mk(0, 100.0, 110.0), LOT)
    assert r["gross_pct"] == pytest.approx(10.0) and r["cost_pct"] > 0 and r["net_pct"] == pytest.approx(r["gross_pct"] - r["cost_pct"])
    assert L.modelled_net_pct(mk(1, 100.0, 110.0, u="COALINDIA"), LOT) is None


def test_accumulating_until_n_and_days_then_decision():
    tr = [mk(i, 100.0, 100.0 + (6 if i % 2 else -4), day=f"2026-10-{21 + i % 5:02d}") for i in range(20)]
    r = L.evaluate(tr, LOT); assert r["verdict"] == "ACCUMULATING" and r["n"] == 20 and r["days"] == 5
    many = [mk(i, 100.0, 103.0, day=f"2026-{10 + (i % 40) // 28:02d}-{1 + (i % 40) % 28:02d}") for i in range(300)]                         # big consistent winners, >=30 days, n>=100
    r = L.evaluate(many, LOT); assert r["n"] == 300 and r["days"] >= 30 and r["verdict"] == "PASS_FOR_NEXT_STAGE" and r["mean_net_pct"] > 0
    losers = [mk(i, 100.0, 99.0, day=f"2026-{10 + (i % 40) // 28:02d}-{1 + (i % 40) % 28:02d}") for i in range(300)]
    assert L.evaluate(losers, LOT)["verdict"] == "FAIL"


def test_hash_manifest_changes_with_content(tmp_path):
    (tmp_path / "a.py").write_text("x=1"); h1 = L.file_hashes(str(tmp_path), ["a.py", "missing.py"])
    (tmp_path / "a.py").write_text("x=2"); h2 = L.file_hashes(str(tmp_path), ["a.py", "missing.py"])
    assert h1["a.py"] != h2["a.py"] and h1["missing.py"] is None


def test_frozen_files_exist_and_loader_is_read_only(tmp_path):
    import sqlite3
    for f, h in L.file_hashes().items(): assert h is not None, f
    p = str(tmp_path / "t.db"); c = sqlite3.connect(p)
    c.execute("CREATE TABLE ai_paper_trades (trade_id TEXT, opened_ts TEXT, closed_ts TEXT, status TEXT, strategy TEXT, underlying TEXT, entry REAL, exit_price REAL)")
    c.execute("INSERT INTO ai_paper_trades VALUES ('a','2026-10-21T04:00:00+00:00','2026-10-21T05:00:00+00:00','CLOSED','AUTOSCALP','NIFTY',100,110)")
    c.execute("INSERT INTO ai_paper_trades VALUES ('b','2026-10-21T04:00:00+00:00','2026-10-21T05:00:00+00:00','CLOSED','MANUAL','NIFTY',100,110)")
    c.execute("INSERT INTO ai_paper_trades VALUES ('c','2026-10-20T04:00:00+00:00','2026-10-20T05:00:00+00:00','CLOSED','AUTOSCALP','NIFTY',100,110)"); c.commit(); c.close()
    assert [t["trade_id"] for t in L.load_trades(p, "2026-10-21")] == ["a"]                                                          # AUTOSCALP only, on/after epoch start
    with pytest.raises(sqlite3.OperationalError):
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True); c.execute("DELETE FROM ai_paper_trades")


def test_module_has_no_network_or_order_code():
    s = open(L.__file__).read()
    for bad in ("requests", "urllib", "socket", "app.execution", "place_order"): assert bad not in s
