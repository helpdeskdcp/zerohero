import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.signal_audit import audit as A, parse as P, tg_export as X

IST = timezone(timedelta(hours=5, minutes=30))
DAY = "2026-10-13"


def ep(hhmm, day=DAY):
    return int(datetime.fromisoformat(f"{day}T{hhmm}:00+05:30").timestamp())


def sig(**kw):
    d = dict(msg_id=1, t=ep("09:32"), date_ist=DAY, text="BUY NIFTY 24500 CE ABOVE 120 SL 100 TGT 140/160", underlying="NIFTY", strike=24500.0, opt_type="CE", side="BUY",
             entry_lo=120.0, entry_hi=120.0, entry_mode="ABOVE", sl=100.0, targets=[140.0, 160.0], expiry=None)
    d.update(kw); return P.Signal(**d)


def mkdb(tmp_path, path_fn, strike=24500.0, ot="CE", expiry=DAY, day=DAY):
    p = str(tmp_path / "u.db")
    if os.path.exists(p): os.remove(p)
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE normalized_option_bars (timestamp TEXT, underlying TEXT, expiry TEXT, strike REAL, option_type TEXT, interval TEXT, open REAL, high REAL, low REAL, close REAL)")
    t = datetime.fromisoformat(f"{day}T09:15:00+05:30")
    while t.hour * 60 + t.minute <= 15 * 60 + 25:
        o, h, l, cl = path_fn(t.strftime("%H:%M"))
        c.execute("INSERT INTO normalized_option_bars VALUES (?,?,?,?,?,?,?,?,?,?)", (t.isoformat(), "NIFTY", expiry, strike, ot, "5minute", o, h, l, cl))
        t += timedelta(minutes=5)
    c.commit(); c.close(); return p


FLAT = lambda hm: (105.0, 106.0, 104.0, 105.0)


# ---------------- parser ----------------
def test_parse_classic_format():
    s = P.parse_signal(7, 1, DAY, "🔥 BUY NIFTY 24,500 CE ABOVE ₹120  SL 100  TGT 140/160/180")
    assert (s.underlying, s.strike, s.opt_type, s.side, s.entry_lo, s.entry_mode, s.sl, s.targets) == ("NIFTY", 24500.0, "CE", "BUY", 120.0, "ABOVE", 100.0, [140.0, 160.0, 180.0])


def test_parse_range_banknifty_t1t2_and_glued_strike():
    s = P.parse_signal(1, 1, DAY, "BANK NIFTY 52000PE near 205-210 | SL: 180 | T1: 240 T2: 270")
    assert (s.underlying, s.strike, s.opt_type, s.entry_lo, s.entry_hi, s.entry_mode, s.sl, s.targets) == ("BANKNIFTY", 52000.0, "PE", 205.0, 210.0, "RANGE", 180.0, [240.0, 270.0])


def test_parse_expiry_and_refuses_to_guess():
    assert P.parse_signal(1, 1, DAY, "NIFTY 24500 CE 21 OCT ABOVE 120 SL 100 TGT 150").expiry == "2026-10-21"
    assert P.parse_signal(1, 1, DAY, "BUY NIFTY 24500 CE SL 100 TGT 140") is None                       # no entry price
    assert P.parse_signal(1, 1, DAY, "BUY NIFTY 24500 CE ABOVE 120 SL 130 TGT 140") is None            # SL above entry: inconsistent
    assert P.parse_signal(1, 1, DAY, "NIFTY looks strong today, 24500 resistance") is None
    assert P.parse_signal(1, 1, DAY, "BUY 24500 CE ABOVE 120 SL 100") is None                          # no underlying


def test_targets_below_entry_are_dropped():
    assert P.parse_signal(1, 1, DAY, "BUY NIFTY 24500 CE ABOVE 120 SL 100 TGT 110/140").targets == [140.0]


@pytest.mark.parametrize("txt,kind,n", [("T1 DONE 🔥🔥", "T_HIT", 1), ("SL HIT", "SL_HIT", None), ("Target 2 achieved", "T_HIT", 2), ("Book profit now", "T_HIT", None), ("exit at cost", "EXIT", None)])
def test_updates(txt, kind, n):
    u = P.parse_update(1, 1, txt, None); assert u and u.kind == kind and u.n_target == n
    assert P.parse_update(1, 1, "good morning friends", None) is None


def test_link_updates_by_reply_and_recency():
    a, b = sig(msg_id=1, t=100), sig(msg_id=2, t=200)
    u1, u2 = P.Update(3, 300, "T_HIT", 1, 1, ""), P.Update(4, 400, "SL_HIT", None, None, "")
    L = P.link_updates([a, b], [u1, u2])
    assert [u.msg_id for u in L[1]] == [3] and [u.msg_id for u in L[2]] == [4]


# ---------------- exports ----------------
def test_export_reader_segments_edits_gaps(tmp_path):
    msgs = [{"id": 1, "type": "message", "date": "2026-10-13T09:31:00", "date_unixtime": str(ep("09:31")), "text": [{"type": "bold", "text": "BUY "}, "NIFTY 24500 CE ABOVE 120 SL 100 TGT 140"], "edited_unixtime": str(ep("09:40"))},
            {"id": 4, "type": "message", "date": "2026-10-13T10:00:00", "text": "T1 DONE", "reply_to_message_id": 1}, {"id": 5, "type": "service", "date": "2026-10-13T10:01:00"}]
    p = tmp_path / "r.json"; p.write_text(json.dumps({"messages": msgs}))
    out, meta = X.load_export(str(p))
    assert out[0]["text"].startswith("BUY NIFTY") and out[0]["edited_t"] == ep("09:40") and out[1]["reply_to"] == 1
    assert out[1]["t"] == ep("10:00") and out[0]["date_ist"] == DAY                                      # naive date treated as IST
    assert meta["id_gaps"] == 2 and meta["edited_messages"] == 1


def test_paste_reader_formats(tmp_path):
    p = tmp_path / "p.txt"; p.write_text("[2026-10-13 09:31] BUY NIFTY 24500 CE ABOVE 120\nSL 100 TGT 140\n13.10.2026 10:00 T1 DONE\n")
    out, meta = X.load_paste(str(p))
    assert len(out) == 2 and "SL 100" in out[0]["text"] and out[0]["t"] == ep("09:31") and out[1]["t"] == ep("10:00")


# ---------------- simulation ----------------
def run(tmp_path, path_fn, s=None, policy="T1", **dbkw):
    st = A.Store(mkdb(tmp_path, path_fn, **dbkw)); return A.audit_signal(st, s or sig(), policy)


def test_message_bar_is_never_used_and_trigger_fill_is_pessimistic(tmp_path):
    def path(hm):
        if hm == "09:30": return (100, 130, 99, 100)                      # contains the 09:32 message: must NOT trigger entry
        if hm == "09:45": return (118, 123, 117, 122)                      # first legal trigger, opens below the level -> fill at the level
        if hm == "10:00": return (125, 141, 124, 140)
        return FLAT(hm)
    r = run(tmp_path, path)
    assert r["status"] == "PRICED" and r["fill"] == 120.0 and r["fill_t"] == ep("09:45") and r["exit_reason"] == "TARGET" and r["exit"] == 140.0
    assert r["net_pts"] == pytest.approx(r["gross_pts"] - r["cost_pts"]) and r["cost_pts"] > 0 and r["net_pts"] < r["gross_pts"] == 20.0
    assert r["lead_min"] == pytest.approx((ep("10:00") - ep("09:32")) / 60)


def test_gap_open_above_trigger_fills_at_open(tmp_path):
    r = run(tmp_path, lambda hm: (130, 135, 128, 132) if hm == "09:40" else FLAT(hm))
    assert r["fill"] == 130.0


def test_same_bar_sl_and_target_is_a_loss(tmp_path):
    r = run(tmp_path, lambda hm: (121, 145, 95, 110) if hm == "09:40" else FLAT(hm))
    assert r["exit_reason"] == "SL" and r["exit"] == 100.0 and r["gross_pts"] == -21.0


def test_gap_through_sl_fills_at_open(tmp_path):
    def path(hm):
        if hm == "09:40": return (121, 125, 119, 122)
        if hm == "09:45": return (90, 95, 85, 92)
        return FLAT(hm)
    r = run(tmp_path, path)
    assert r["exit_reason"] == "SL" and r["exit"] == 90.0


def test_eod_exit_and_not_triggered_and_missed(tmp_path):
    def path(hm):
        if hm == "09:40": return (121, 125, 119, 122)
        return (122, 130, 110, 120)
    r = run(tmp_path, path); assert r["exit_reason"] == "EOD" and r["exit_t"] == ep("15:10")
    assert run(tmp_path, FLAT)["status"] == "NOT_TRIGGERED"
    s = sig(entry_mode="NEAR", entry_lo=85.0, entry_hi=90.0, sl=70.0, targets=[110.0])
    assert run(tmp_path, FLAT, s)["status"] == "MISSED"                                                  # opens 100 > 90*1.05


def test_near_entry_fills_next_bar_open(tmp_path):
    s = sig(entry_mode="NEAR", entry_lo=99.0, entry_hi=100.0, sl=90.0, targets=[104.0])
    r = run(tmp_path, lambda hm: (100, 105, 99, 104) if hm == "09:35" else FLAT(hm), s)
    assert r["status"] == "PRICED" and r["fill_t"] == ep("09:35") and r["exit_reason"] == "TARGET"


def test_posted_after_t1_is_flagged(tmp_path):
    r = run(tmp_path, lambda hm: (130, 150, 128, 145) if hm == "09:20" else (140, 150, 90, 100) if hm == "09:40" else FLAT(hm))
    assert r["posted_after_t1"] is True and r["pre_msg_max_high"] == 150


def test_no_data_cases(tmp_path):
    st = A.Store(mkdb(tmp_path, FLAT))
    assert A.audit_signal(st, sig(underlying="BANKNIFTY"))["status"].startswith("NO_DATA:underlying_not_supported")
    assert A.audit_signal(st, sig(strike=99999.0))["status"].startswith("NO_DATA:strike_or_day")
    assert A.audit_signal(st, sig(date_ist="2026-12-01"))["status"].startswith("NO_DATA:no_expiry")
    assert A.audit_signal(st, sig(side="SELL"))["status"].startswith("NO_DATA:short_option")


def test_store_is_read_only(tmp_path):
    st = A.Store(mkdb(tmp_path, FLAT))
    with pytest.raises(sqlite3.OperationalError): st.c.execute("DELETE FROM normalized_option_bars")


def test_summary_needs_three_priced_and_reports_honest_counts(tmp_path):
    rows = [{"msg_id": i, "date": DAY, "status": "PRICED", "net_pts": v, "gross_pts": v + 2, "exit_reason": "SL" if v < 0 else "TARGET", "posted_after_t1": False, "lead_min": 8.0}
            for i, v in enumerate([20, -15, 18, -15])] + [{"msg_id": 9, "date": DAY, "status": "NO_DATA:strike_or_day_not_in_store"}]
    s = A.summarize(rows, {0: [P.Update(1, 1, "T_HIT", 1, None)]}, {}, {})
    assert s["priced"] == 4 and s["by_status"]["NO_DATA"] == 1 and s["performance"]["n"] == 4 and s["timing"]["lead_5_to_15"] == 4
    assert s["performance"]["loss_count_share"] == 0.5 and s["channel_claims"]["no_outcome_posted"] == 4 + 1 - 1
    assert A.summarize(rows[:2], {}, {}, {})["performance"] is None


def test_cli_end_to_end(tmp_path):
    db = mkdb(tmp_path, lambda hm: (121, 145, 119, 140) if hm == "09:40" else FLAT(hm))
    f = tmp_path / "msgs.txt"; f.write_text("[2026-10-13 09:32] BUY NIFTY 24500 CE ABOVE 120 SL 100 TGT 140/160\n[2026-10-13 09:50] T1 DONE\n[2026-10-13 10:00] NIFTY 24400 CE 85 SL 70\n")
    out = tmp_path / "o"
    r = subprocess.run([sys.executable, "scripts/telegram_signal_audit.py", str(f), "--format", "paste", "--db", db, "--out", str(out)], capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert r.returncode == 0, r.stderr[-400:]
    s = json.load(open(out / "summary.json"))
    assert s["parse"]["signals_parsed"] == 1 and s["parse"]["updates_parsed"] == 1 and s["priced"] == 1 and s["performance"] is None
    assert (out / "signals.csv").exists() and "UNVERIFIED" in (out / "summary.md").read_text()
