import json

from app.evidence_feed import first_post as FP


def rec(ok=True, kind="AUTOSCALP", text="🧾 <b>ZeroHero AI — PAPER signal</b> · x\nID: <code>T1</code> · t\n<b>NATURALGAS 290 PE</b> · expiry 23OCT2026\nEntry (premium) 9.8 · SL 9.7 · T1 10.0\n<i>d</i>"):
    return {"type": "EVIDENCE_POST", "kind": kind, "id": "T1", "signal_ts": 1_792_200_000.0, "text": text, "telegram": {"ok": ok, "message_id": 12 if ok else None, "reason": None if ok else "boom"}}


def test_first_post_reads_only_evidence_posts_and_handles_missing(tmp_path):
    p = tmp_path / "posts.jsonl"; assert FP.first_post(str(p)) is None
    p.write_text(json.dumps({"type": "OTHER"}) + "\n" + json.dumps(rec()) + "\n" + json.dumps(rec(kind="ARM")) + "\n"); r = FP.first_post(str(p)); assert r["kind"] == "AUTOSCALP"


def test_message_reports_success_and_failure_without_html():
    m = FP.build_message(rec()); assert "pahila signal" in m and "message_id=12" in m and "NATURALGAS 290 PE" in m and "<b>" not in m and "IST" in m
    f = FP.build_message(rec(ok=False)); assert "FAIL" in f and "reason=boom" in f
