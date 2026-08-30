"""P9 -- autoscalp API endpoints + frontend wiring."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


def test_autoscalp_endpoints_present_and_paper(fresh_db):
    from app import main
    st = main.api_autoscalp_status()
    assert st["live_trading"] is False and st["paper_mode"] is True
    assert st["armed"] is False
    assert main.api_autoscalp_signals(limit=50) == []
    assert main.api_autoscalp_snapshots(limit=50) == []
    cfg = main.api_autoscalp_get_config()
    assert "symbols" in cfg and cfg["symbols"] == ["NIFTY"]
    # arm/disarm toggles the shared setting; PAPER only, no live path
    main.autoscalp.arm()
    assert main.api_autoscalp_status()["armed"] is True
    main.api_autoscalp_disarm()
    assert main.api_autoscalp_status()["armed"] is False


def test_autoscalp_config_validation(fresh_db):
    from app import main
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        main.api_autoscalp_set_config({"bogus": 1})
    out = main.api_autoscalp_set_config({"decide_every_sec": 45})
    assert out["decide_every_sec"] == 45


def test_no_endpoint_enables_live():
    """Grep guard: nothing under /api/autoscalp/* flips a LIVE flag."""
    src = (Path(__file__).parents[1] / "app" / "main.py").read_text()
    seg = src[src.index("Autonomous scalper (P7"):src.index("External position tracker")]
    for bad in ("execution_mode", "CHANAKYA_ALLOW_LIVE", "live_enabled", "market_entry",
                "AngelOneBroker", "execution_enabled", "limit_entry"):
        assert bad not in seg, f"autoscalp endpoints must not reference {bad}"


def test_frontend_autoscalp_view_wired():
    root = Path(__file__).parents[2]
    html = (root / "frontend" / "index.html").read_text()
    js = (root / "frontend" / "static" / "js" / "app.js").read_text()
    assert 'data-view="autoscalp"' in html and 'id="view-autoscalp"' in html
    assert 'id="asArmBtn"' in html and 'id="asKillBtn"' in html
    assert "loadAutoscalp" in js and "/api/autoscalp/status" in js
    assert 'if (view === "autoscalp")' in js
