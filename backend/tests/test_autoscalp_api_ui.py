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
    assert "symbols" in cfg and "NIFTY" in cfg["symbols"]
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


def test_status_label_and_compact_modes(fresh_db):
    from app import main
    # inject a couple of marks to exercise the label/compact helpers
    fake = {"feed": {"connected": True, "last_msg_age_sec": 0.2,
                     "desired_tokens": ["99926000", "568245"],
                     "active_tokens": ["99926000", "568245"],
                     "marks": {"99926000": {"ltp": 24080.4, "age_sec": 0.2, "fresh": True},
                               "568245": {"ltp": 274.4, "age_sec": 9.0, "fresh": False}}},
            "config": {"a": 1}, "safeguards": {"config": {"x": 1}, "trades_today": 0}}
    labelled = main._label_marks({k: (dict(v) if isinstance(v, dict) else v) for k, v in fake.items()})
    assert labelled["feed"]["marks"]["99926000"]["label"] == "NIFTY"
    assert labelled["feed"]["marks"]["568245"]["label"] == "NATGAS"

    comp = main._compact({k: (dict(v) if isinstance(v, dict) else v) for k, v in fake.items()})
    assert comp["feed"]["marks"] == {"NIFTY": 24080.4, "NATGAS": 274.4}
    assert comp["feed"]["n_desired"] == 2 and comp["feed"]["stale_marks"] == 1
    assert "desired_tokens" not in comp["feed"] and "config" not in comp
    assert "config" not in comp["safeguards"]


def test_market_calendar_endpoint(fresh_db):
    from app import main
    out = main.api_market_calendar()
    assert set(out["segments"]) >= {"NSE", "MCX"}
    assert isinstance(out["restart_allowed"], bool)


def test_autoscalp_universe_grouped(fresh_db):
    from app import main
    u = main.api_autoscalp_universe()
    assert "NIFTY" in u["watchlist"] and "NATURALGAS" in u["watchlist"]
    assert set(u["groups"]) == {"NSE Index", "MCX", "Equity (F&O)"}
    assert "NATURALGAS" in u["groups"]["MCX"]
    # F&O equity list resolves off the instrument master (may be empty if the
    # cache is absent in a sandbox) but the key must exist
    assert isinstance(u["groups"]["Equity (F&O)"], list)


def test_watchlist_add_remove(fresh_db):
    from app import main
    from fastapi import HTTPException
    base = list(main.autoscalp.get_config()["symbols"])
    out = main.api_autoscalp_watchlist({"symbol": "reliance", "action": "add"})
    assert "RELIANCE" in out["symbols"]
    out = main.api_autoscalp_watchlist({"symbol": "RELIANCE", "action": "add"})   # idempotent
    assert out["symbols"].count("RELIANCE") == 1
    out = main.api_autoscalp_watchlist({"symbol": "RELIANCE", "action": "remove"})
    assert "RELIANCE" not in out["symbols"] and set(out["symbols"]) == set(base)
    with pytest.raises(HTTPException):
        main.api_autoscalp_watchlist({"symbol": "X", "action": "bogus"})
    # cannot empty the watchlist
    for s in list(main.autoscalp.get_config()["symbols"])[:-1]:
        main.api_autoscalp_watchlist({"symbol": s, "action": "remove"})
    with pytest.raises(HTTPException):
        last = main.autoscalp.get_config()["symbols"][0]
        main.api_autoscalp_watchlist({"symbol": last, "action": "remove"})


def test_symbol_profiles_leave_nifty_frozen(fresh_db):
    from app.autoscalp.runner import DEFAULT_CONFIG
    sp = DEFAULT_CONFIG["symbol_profiles"]
    assert "NIFTY" not in sp                              # NIFTY runs on validated defaults
    assert sp["NATURALGAS"]["max_hold_sec"] == 1800
    assert sp["CRUDEOIL"]["ev"]["min_ev_r"] == 0.15
    # symbol_profiles is a known config key (settable via /api/autoscalp/config)
    from app.autoscalp.runner import AutoScalpRunner
    r = AutoScalpRunner()
    out = r.set_config({"symbol_profiles": {"BANKNIFTY": {"max_hold_sec": 900}}})
    assert out["symbol_profiles"]["BANKNIFTY"]["max_hold_sec"] == 900


def test_no_endpoint_enables_live():
    """Grep guard: nothing under /api/autoscalp/* flips a LIVE flag."""
    # autoscalp's routes live in app/api/autoscalp_routes.py (split out of the
    # old main.py "Autonomous scalper (P7)" section) -- scan that file whole.
    seg = (Path(__file__).parents[1] / "app" / "api" / "autoscalp_routes.py").read_text()
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
