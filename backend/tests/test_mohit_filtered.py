import json
from datetime import datetime

from app.arena import mohit_filtered as MF
from app.arena import mohit_forward as FT


def sig(i, day, hhmm, und="NIFTY", ot="CE"):
    return {"id": i, "participant": "INSTITUTION_TRADER", "underlying": und, "strike": 23000.0, "option_type": ot, "t": datetime.fromisoformat(f"{day}T{hhmm}:00+05:30").timestamp()}


def _write(path, rows): open(path, "w").write("\n".join(json.dumps(r) for r in rows) + "\n")


def _tags(tmp_path, sigs, decide):
    p = tmp_path / "tags.jsonl"
    _write(p, [{"id": s["id"], "post_ist": datetime.fromtimestamp(s["t"]).strftime("%Y-%m-%d %H:%M:%S"), "period": "FORWARD" if s["id"].startswith("F") else "IN_SAMPLE", "decision": decide(s), "reasons": [] if decide(s) == "ACCEPT" else ["X"]} for s in sigs])
    return str(p)


def test_board_counts_classes_money_periods_and_reconciles_with_the_original(tmp_path):
    S = [sig("A1", "2026-10-29", "09:30"), sig("A2", "2026-10-29", "09:40"), sig("R1", "2026-10-29", "13:00"), sig("R2", "2026-10-29", "13:10"), sig("N1", "2026-10-29", "13:20"), sig("F1", "2026-11-02", "09:30"), sig("F2", "2026-11-02", "12:00")]
    res = {"A1": {"status": "SETTLED", "net_rs": 1000.0}, "A2": {"status": "SETTLED", "net_rs": -300.0}, "R1": {"status": "SETTLED", "net_rs": 500.0}, "R2": {"status": "SETTLED", "net_rs": -800.0},
           "N1": {"status": "NOT_TRIGGERED"}, "F1": {"status": "SETTLED", "net_rs": 200.0}, "F2": {"status": "SETTLED", "net_rs": 100.0}}
    tp = _tags(tmp_path, S[:5] + S[5:], lambda s: "ACCEPT" if s["id"] in ("A1", "A2", "F1") else "REJECT")
    out = MF.build(tags_path=tp, controls_path=str(tmp_path / "none.jsonl"), results=dict(res), signals=S, snapshot_path=str(tmp_path / "none2.jsonl"))
    ins, fw = out["periods"]["IN_SAMPLE"], out["periods"]["FORWARD"]
    assert (ins["true_accept"], ins["false_accept"], ins["true_reject"], ins["false_reject"]) == (1, 1, 1, 1) and ins["profit_filtered_out_rs"] == 500.0 and ins["losses_avoided_rs"] == -800.0 and ins["loss_taken_by_false_accepts_rs"] == -300.0
    assert ins["accept_stats"]["settled"] == 2 and ins["reject_stats"]["calls"] == 3 and ins["reject_stats"]["not_executable_or_no_data"] == 1 and ins["filter_accuracy"] == 0.5
    assert (fw["accept"], fw["reject"], fw["true_accept"], fw["false_reject"]) == (1, 1, 1, 1)
    o = out["original"]; assert o["calls_total"] == 7 and o["settled"] == 6 and o["net_total_rs"] == 700.0          # the original row is the sum over ALL calls, rejected ones included
    assert abs(sum(p[k]["net_total_rs"] for p in out["periods"].values() for k in ("accept_stats", "reject_stats") if p[k].get("settled")) - o["net_total_rs"]) < 1e-6
    assert out["gate"]["status"] == "NOT_YET" and [r["id"] for r in out["rows"]][0] == "A1"


def test_untagged_calls_are_pending_never_scored_as_accept_or_reject_and_a_frozen_outcome_beats_a_recomputation(tmp_path):
    S = [sig("A1", "2026-10-29", "09:30"), sig("F1", "2026-11-02", "09:30")]
    tp = _tags(tmp_path, S[:1], lambda s: "ACCEPT")
    snap = tmp_path / "snap.jsonl"; _write(snap, [{"id": "A1", "status": "SETTLED", "net_rs": 1234.0}])
    out = MF.build(tags_path=tp, controls_path=str(tmp_path / "c.jsonl"), results={"A1": {"status": "NO_DATA:no_snapshot_after_signal"}, "F1": {"status": "SETTLED", "net_rs": 50.0}}, signals=S, snapshot_path=str(snap))
    assert out["periods"]["IN_SAMPLE"]["accept_stats"]["net_total_rs"] == 1234.0                                   # the frozen outcome, not the (now missing) live quotes
    f = out["periods"]["FORWARD"]; assert f["pending_tag"] == 1 and f["accept"] == 0 and f["reject"] == 0
    assert [r["verified_class"] for r in out["rows"]] == ["TRUE_ACCEPT", "PENDING_TAG"]


def test_control_tags_are_append_only_and_follow_their_definitions(tmp_path):
    p = str(tmp_path / "ctl.jsonl"); S = [sig("A", "2026-10-29", "09:30"), sig("B", "2026-10-29", "13:00", und="CRUDEOIL"), sig("C", "2026-10-29", "10:10", und="CRUDEOIL")]
    FT.tag_controls(S, p); first = open(p).read(); assert FT.tag_controls(S, p) == [] and open(p).read() == first
    rows = {json.loads(l)["id"]: json.loads(l) for l in open(p)}
    assert (rows["A"]["time_only"], rows["A"]["index_only"]) == ("ACCEPT", "ACCEPT") and (rows["B"]["time_only"], rows["B"]["index_only"]) == ("REJECT", "REJECT") and (rows["C"]["time_only"], rows["C"]["index_only"]) == ("ACCEPT", "REJECT")


def test_snapshot_keeps_settled_results_forever_and_other_statuses_only_after_their_day(tmp_path):
    p = str(tmp_path / "snap.jsonl"); S = [sig("A", "2026-11-02", "09:30"), sig("B", "2026-11-02", "10:00"), sig("C", "2026-10-30", "10:00")]
    R = {"A": {"status": "SETTLED", "net_rs": 5.0}, "B": {"status": "NOT_TRIGGERED"}, "C": {"status": "NOT_TRIGGERED"}}
    assert FT.snapshot_results(R, S, p, now=datetime.fromisoformat("2026-11-02T20:00:00+05:30")) == 2          # A settled; C is from an earlier day; B (today, not final) waits
    assert set(FT.load_snapshot(p)) == {"A", "C"} and FT.snapshot_results(R, S, p, now=datetime.fromisoformat("2026-11-03T08:00:00+05:30")) == 1 and set(FT.load_snapshot(p)) == {"A", "B", "C"}
