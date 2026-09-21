"""app.reverse_engineering.schema -- isolated sqlite store, own tmp DB."""
import pytest

from app.reverse_engineering import schema as s


def _mk(**over):
    data = {"source": "SCREENSHOT:logic_trade", "capture_timestamp": "2026-09-21T00:00:00+00:00"}
    data.update(over)
    return s.ResearchEvent(**data)


def test_round_trip_preserves_fields(tmp_path):
    db_path = str(tmp_path / "research_events.db")
    ev = _mk(underlying="NIFTY", strike=24050.0, ce_pe="PE", entry=64.0, target=80.0,
            underlying_ohlc={"o": 1, "h": 2, "l": 0.5, "c": 1.5})
    rid = s.insert_event(ev, db_path=db_path)
    assert rid > 0
    rows = s.query_events(db_path=db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["underlying"] == "NIFTY"
    assert row["strike"] == 24050.0
    assert row["ce_pe"] == "PE"
    assert row["underlying_ohlc"] == {"o": 1, "h": 2, "l": 0.5, "c": 1.5}
    assert row["schema_version"] == s.SCHEMA_VERSION


def test_provenance_is_mandatory(tmp_path):
    db_path = str(tmp_path / "research_events.db")
    with pytest.raises(ValueError):
        s.insert_event(s.ResearchEvent(source="", capture_timestamp="2026-01-01T00:00:00Z"),
                       db_path=db_path)
    with pytest.raises(ValueError):
        s.insert_event(s.ResearchEvent(source="X", capture_timestamp=""), db_path=db_path)


def test_query_filters_by_underlying(tmp_path):
    db_path = str(tmp_path / "research_events.db")
    s.insert_event(_mk(underlying="NIFTY"), db_path=db_path)
    s.insert_event(_mk(underlying="SENSEX"), db_path=db_path)
    rows = s.query_events(underlying="SENSEX", db_path=db_path)
    assert len(rows) == 1 and rows[0]["underlying"] == "SENSEX"


def test_migrate_is_idempotent_on_existing_db(tmp_path):
    db_path = str(tmp_path / "research_events.db")
    s.migrate(db_path)
    s.migrate(db_path)   # must not raise on a second call
    assert s.count_events(db_path=db_path) == 0


def test_count_events(tmp_path):
    db_path = str(tmp_path / "research_events.db")
    assert s.count_events(db_path=db_path) == 0
    s.insert_event(_mk(), db_path=db_path)
    assert s.count_events(db_path=db_path) == 1


def test_never_touches_the_default_path_constant_directly(tmp_path, monkeypatch):
    """Sanity check the module-level default points at data/research_events.db,
    never at chanakya.db or market_history.db."""
    assert "chanakya.db" not in s._DEFAULT_PATH
    assert "market_history.db" not in s._DEFAULT_PATH
    assert s._DEFAULT_PATH.endswith("research_events.db")
