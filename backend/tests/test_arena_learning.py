import json
from datetime import datetime, timedelta, timezone

from app.arena import learning as L

IST = timezone(timedelta(hours=5, minutes=30))
def ep(day, hhmm): return datetime.fromisoformat(f"{day}T{hhmm}:00+05:30").timestamp()


def test_excluded_participant_day_is_removed_from_learning_sets_and_other_days_stay(tmp_path):
    f = tmp_path / "x.json"; f.write_text(json.dumps({"INSTITUTION_TRADER": {"days": ["2026-10-30"]}}))
    sigs = [{"id": "a", "participant": "INSTITUTION_TRADER", "t": ep("2026-10-30", "09:18")}, {"id": "b", "participant": "INSTITUTION_TRADER", "t": ep("2026-10-29", "09:18")},
            {"id": "c", "participant": "DHANANJAY_DALVI", "t": ep("2026-10-30", "09:30")}, {"id": "d", "participant": "INSTITUTION_TRADER", "t": ep("2026-10-30", "23:59")}]
    assert [s["id"] for s in L.for_learning(sigs, str(f))] == ["b", "c"]


def test_missing_or_broken_file_excludes_nothing(tmp_path):
    sigs = [{"id": "a", "participant": "P", "t": ep("2026-10-30", "10:00")}]
    assert L.for_learning(sigs, str(tmp_path / "none.json")) == sigs
    bad = tmp_path / "bad.json"; bad.write_text("{not json"); assert L.for_learning(sigs, str(bad)) == sigs


def test_the_real_file_excludes_mohits_30_oct_posts():
    assert L.is_excluded("INSTITUTION_TRADER", ep("2026-10-30", "11:57")) and not L.is_excluded("INSTITUTION_TRADER", ep("2026-10-29", "11:57"))
