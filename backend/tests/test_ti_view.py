import json, os, sqlite3, tempfile, time
from app.arena import ti_view as T
def _dir():
    d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "docs")); os.makedirs(os.path.join(d, "data"))
    with open(os.path.join(d, "docs", "experiment_ledger.jsonl"), "w") as f:
        f.write(json.dumps({"id": "E001", "status": "PRE_REGISTERED", "hypothesis": "h"}) + "\n"); f.write(json.dumps({"id": "E001", "status": "DONE", "verdict": "REJECT"}) + "\n"); f.write(json.dumps({"id": "E002", "status": "DONE", "verdict": "REJECT"}) + "\n")
    json.dump({"grid": {"normal": {"mfe_q": {"0.5": .056, "0.75": .109, "0.9": .187, "0.95": .255}, "cells": {"sl0.2_t0.1": {"p_target_first_cons": .274, "p_stop": .065}}}, "expiry_day": {"mfe_q": {"0.5": .16, "0.75": .34, "0.9": .56, "0.95": .74}, "cells": {}}}}, open(os.path.join(d, "data", "levels.json"), "w"))
    now = time.time(); open(os.path.join(d, "data", "decisions.jsonl"), "w").write(json.dumps({"ts": now - 30, "ist": "x", "action": "NO TRADE", "confidence": 0.41, "reasons": ["model rank below the 90th-percentile threshold (0.413 < 0.442)"], "mode": "FORWARD_PAPER"}) + "\n")
    c = sqlite3.connect(os.path.join(d, "data", "zh2.db")); c.execute("create table paper_trades(id integer primary key, opened_ts, closed_ts, underlying, expiry, strike, option_type, lots, entry, sl, t1, t2, t3, t4, exit, exit_reason, gross_rs, cost_rs, net_rs)")
    c.execute("insert into paper_trades(opened_ts, closed_ts, expiry, strike, option_type, entry, sl, t1, t2, t3, t4, exit, exit_reason, net_rs) values (1000, 1900, '2026-11-09', 24400, 'CE', 100, 80, 105, 110, 120, 130, 98, 'SL', -300)"); c.commit(); c.close(); return d
def test_dashboard_says_no_validated_edge_and_labels_the_decision():
    d = _dir(); v = T.view("NIFTY", "2026-11-09", 24400, "CE", ti_dir=d); rows = dict((a, b) for a, b in v["dash"])
    assert v["covered"] and not v["edge"]["validated"] and "NO VALIDATED EDGE (2/2" in rows["Edge"] and rows["Now"] == "NO TRADE" and "0.413" in rows["Why"] and v["decision"]["age_s"] in range(25, 40)
def test_reference_levels_are_marked_as_not_a_signal_and_switch_on_expiry_day():
    d = _dir(); today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 19800)); n = T.view("NIFTY", "2026-11-09", None, None, ti_dir=d)["reference"]; e = T.view("NIFTY", today, None, None, ti_dir=d)["reference"]
    assert n["regime"] == "normal" and e["regime"] == "expiry_day" and "NOT a signal" in n["note"] and len(n["targets_pct"]) == 4 and n["sl_pct"] == -0.2
def test_paper_trades_are_filtered_to_the_contract_and_other_underlyings_are_not_covered():
    d = _dir(); assert len(T.view("NIFTY", "2026-11-09", 24400, "CE", ti_dir=d)["paper"]["trades"]) == 1 and T.view("NIFTY", "2026-11-09", 24500, "CE", ti_dir=d)["paper"]["trades"] == []
    o = T.view("CRUDEOIL", ti_dir=d); assert o["covered"] and o["decision"] is None and o["reference"] is None and any("no decision" in r[1] for r in o["dash"])        # every Arena instrument is covered; no data yet -> honest empty state
    assert not T.view("FOO", ti_dir=d)["covered"]
def test_missing_directory_does_not_raise():
    v = T.view("NIFTY", ti_dir="/nonexistent/x"); assert v["dash"] and not v["edge"]["validated"]

def test_other_instruments_show_their_own_decision_context_and_calibrated_levels():
    d = _dir(); now = time.time()
    with open(os.path.join(d, "data", "decisions.jsonl"), "a") as f:
        f.write(json.dumps({"ts": now - 20, "ist": "x", "underlying": "CRUDEOIL", "action": "NO TRADE", "mode": "CONTEXT_ONLY", "reasons": ["no model has been trained or validated for CRUDEOIL"], "expected_cost_rs": 184.5, "breakeven_points": 1.845, "cost_verified": True, "atm": 8500, "regime": "normal-day", "legs": {"CE": {"spread_pct": 0.19, "tradable": True}}}) + "\n")
    lv = json.load(open(os.path.join(d, "data", "levels.json"))); lv["instruments"] = {"CRUDEOIL": {"source": "Drive quote archive", "normal": {"n": 900, "mfe_q": {"0.5": .03, "0.75": .06, "0.9": .1, "0.95": .13}, "cells": {}}}}; json.dump(lv, open(os.path.join(d, "data", "levels.json"), "w"))
    v = T.view("CRUDEOIL", ti_dir=d); rows = dict((a, b) for a, b in v["dash"])
    assert v["decision"]["action"] == "NO TRADE" and "Rs184 /lot" not in rows["Cost"] and "184" in rows["Cost"] and "8500" in rows["ATM"] and "0.19%" in rows["ATM"] and v["reference"]["n"] == 900 and v["reference"]["targets_pct"][0] == 0.03 and "Drive quote archive" in v["reference"]["note"]
    nifty = T.view("NIFTY", ti_dir=d); assert nifty["decision"]["action"] == "NO TRADE" and "0.413" in dict((a, b) for a, b in nifty["dash"])["Why"]                       # NIFTY row is not replaced by the crude row
