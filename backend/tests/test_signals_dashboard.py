"""Unified Signal Dashboard aggregator -- offline sanity. No network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import signals_dashboard as sd  # noqa: E402


def test_dir_classifier():
    assert sd._dir("BUY_CE") == "BULLISH"
    assert sd._dir("BUY_PE") == "BEARISH"
    assert sd._dir("BUY_CE", "BUY_PE") == "NEUTRAL"      # conflicting -> neutral
    assert sd._dir(None, "", "NONE") == "NEUTRAL"
    assert sd._dir("SUPPORT_BREAKDOWN") == "BEARISH"


def test_build_shape_and_readonly():
    d = sd.build()
    assert "rows" in d and "generated_at" in d and "note" in d
    assert "live_trading=false" in d["note"]
    for r in d["rows"]:
        for k in ("symbol", "autoscalp", "hcs", "confluence", "orderflow",
                  "agreement", "agreement_votes"):
            assert k in r, k
        assert r["agreement"] in ("BULLISH", "BEARISH", "MIXED", "NEUTRAL")
        av = r["agreement_votes"]
        assert av["bullish"] + av["bearish"] <= av["n"] + 1  # votes are a subset


def test_build_is_cached():
    a = sd.build()
    b = sd.build()
    # second call within TTL returns the cached payload
    assert b.get("cached") is True or a.get("cached") is True or a["generated_at"] == b["generated_at"]


def test_a_bad_subsource_does_not_crash(monkeypatch):
    monkeypatch.setattr(sd, "_confluence_by_symbol", lambda syms: {"_error": "boom"})
    sd._cache["data"] = None
    d = sd.build()
    assert d["errors"].get("confluence") == "boom"
    assert isinstance(d["rows"], list)
