"""
Unifying autoscalp's option-chain fetch onto market_hub.get_chain() (2026-09-05
architecture-review follow-up): previously app/runtime.py._autoscalp_chain
called market_data.selection_snapshot() directly -- a second, independent
broker-facing chain read alongside market_hub's own (used by
mathematical_confluence/smart_index_scalper). Neither path had ANY test
coverage before this change, despite feeding the currently-armed live
AutoScalp PAPER engine's actual trading decisions -- so this file covers both
market_hub.get_chain() itself and the _autoscalp_chain adapter that consumes
it, with particular attention to NOT silently changing autoscalp's behavior.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import app.market_hub as market_hub   # noqa: E402
import app.market_data as market_data  # noqa: E402
import app.runtime as runtime          # noqa: E402


# ---------------------------------------------------------------- get_chain()
def test_get_chain_prefers_histcap_and_carries_token(monkeypatch):
    hc_result = {
        "chain": [{"strike": 24000.0, "ce_ltp": 120.0, "ce_oi": 5000.0, "ce_token": "111",
                  "ce_oi_source": "HISTCAP", "ce_oi_timestamp": "2026-09-05T05:00:00Z",
                  "pe_ltp": 90.0, "pe_oi": 4000.0, "pe_token": "112",
                  "expiry": "10SEP2026"}],
        "source": "HISTCAP", "expiry": "10SEP2026", "age_sec": 12,
        "oi_coverage": {"legs": 2, "with_oi": 2, "ratio": 1.0},
    }
    monkeypatch.setattr(market_hub, "_hist_chain", lambda sym, sess: hc_result)

    def _boom(*a, **kw):
        raise AssertionError("REST fallback must not be reached when histcap answers")
    monkeypatch.setattr(market_hub, "_throttle", _boom)

    out = market_hub.get_chain("NIFTY", window=2, allow_rest_fallback=True)
    assert out["chain"][0]["ce_token"] == "111"
    assert out["chain"][0]["ce_oi_source"] == "HISTCAP"
    assert out["expiry"] == "10SEP2026"
    assert out["data_quality_note"].startswith("ACTUAL (histcap")


def test_get_chain_auto_roll_bypasses_histcap_even_if_fresh(monkeypatch):
    """histcap has no concept of 'skip the 0-DTE contract' -- AUTO_ROLL must
    always go to the REST fallback, which does honour expiry_mode."""
    called = {"hist_chain": 0}

    def _hc(sym, sess):
        called["hist_chain"] += 1
        return {"chain": [{"strike": 1.0}], "expiry": "X", "age_sec": 1,
                "oi_coverage": {}}
    monkeypatch.setattr(market_hub, "_hist_chain", _hc)
    monkeypatch.setattr(market_hub, "_throttle", lambda key: True)

    seen = {}
    def _fake_selection_snapshot(sdk, mkt, sym, *, expiry, option_type, window, instrument):
        seen["expiry"] = expiry
        return {"chain": [], "expiry": "17SEP2026", "atm": 24000, "spot": 24010,
                "oi_coverage": {}}
    monkeypatch.setattr(market_data, "selection_snapshot", _fake_selection_snapshot)

    out = market_hub.get_chain("NIFTY", expiry_mode="AUTO_ROLL", sdk=object())
    assert called["hist_chain"] == 0, "histcap shortcut must be skipped for AUTO_ROLL"
    assert seen["expiry"] == "AUTO_ROLL"
    assert out["expiry"] == "17SEP2026"


def test_get_chain_rest_fallback_when_histcap_empty(monkeypatch):
    monkeypatch.setattr(market_hub, "_hist_chain", lambda sym, sess: None)
    monkeypatch.setattr(market_hub, "_throttle", lambda key: True)

    def _fake_selection_snapshot(sdk, mkt, sym, *, expiry, option_type, window, instrument):
        return {"chain": [{"strike": 24000.0, "ce_token": "999", "ce_ltp": 100.0,
                          "ce_oi_source": "ANGELONE_OPTION_GREEK"}],
                "expiry": "10SEP2026", "atm": 24000, "spot": 24005, "oi_coverage": {}}
    monkeypatch.setattr(market_data, "selection_snapshot", _fake_selection_snapshot)

    out = market_hub.get_chain("NIFTY", sdk=object())
    assert out["chain"][0]["ce_token"] == "999"
    assert out["chain"][0]["ce_oi_source"] == "ANGELONE_OPTION_GREEK"
    assert out["data_quality_note"] == "ACTUAL (REST)"


def test_get_chain_throttled_returns_empty_not_error(monkeypatch):
    monkeypatch.setattr(market_hub, "_hist_chain", lambda sym, sess: None)
    monkeypatch.setattr(market_hub, "_throttle", lambda key: False)   # rate-limited
    out = market_hub.get_chain("NIFTY", sdk=object())
    assert out["chain"] == []
    assert out["error"] is None


def test_get_chain_no_sdk_no_fallback_returns_empty(monkeypatch):
    """When sdk=None, get_chain() tries to resolve one via _market_sdk(). This
    test runs on the same box as the live service (real broker creds in
    .env) -- an earlier version of this test let that resolution actually
    succeed and made a REAL broker call. Explicitly force resolution to fail
    so this stays offline regardless of what credentials happen to be
    present in the environment it runs in."""
    monkeypatch.setattr(market_hub, "_hist_chain", lambda sym, sess: None)
    import app.connectors.angelone as angelone_mod

    def _no_sdk(require_auth=False):
        return None
    monkeypatch.setattr(angelone_mod, "_market_sdk", _no_sdk)

    out = market_hub.get_chain("NIFTY", allow_rest_fallback=True, sdk=None)
    assert out["chain"] == []


# ---------------------------------------------------------------- _autoscalp_chain()
def _hc_row(strike, **over):
    row = {"strike": strike, "expiry": "10SEP2026",
          "ce_ltp": 120.0, "ce_oi": 5000.0, "ce_oi_change": 100.0, "ce_volume": 10.0,
          "ce_token": "111", "ce_oi_status": "AVAILABLE", "ce_oi_source": "HISTCAP",
          "ce_oi_timestamp": "2026-09-05T05:00:00Z",
          "ce_iv": None, "ce_delta": None, "ce_gamma": None, "ce_theta": None, "ce_vega": None,
          "ce_greeks_source": "UNAVAILABLE",
          "pe_ltp": 90.0, "pe_oi": 4000.0, "pe_oi_change": -50.0, "pe_volume": 8.0,
          "pe_token": "112", "pe_oi_status": "AVAILABLE", "pe_oi_source": "HISTCAP",
          "pe_oi_timestamp": "2026-09-05T05:00:00Z",
          "pe_iv": None, "pe_delta": None, "pe_gamma": None, "pe_theta": None, "pe_vega": None,
          "pe_greeks_source": "UNAVAILABLE"}
    row.update(over)
    return row


def test_autoscalp_chain_builds_nested_shape_from_histcap_rows(monkeypatch):
    monkeypatch.setattr(runtime.market_hub, "get_chain",
                        lambda sym, **kw: {"chain": [_hc_row(24000.0)], "expiry": "10SEP2026"})
    monkeypatch.setattr(runtime, "_merge_broker_greeks", lambda *a, **k: None)

    out = runtime._autoscalp_chain("NIFTY", 24000, 2, market="NSE")
    assert len(out) == 1
    leg = out[0]
    assert leg["strike"] == 24000.0
    assert leg["ce"]["ltp"] == 120.0
    assert leg["ce"]["oi"] == 5000.0
    assert leg["ce"]["oi_chg"] == 100.0
    assert leg["ce"]["token"] == "111"
    assert leg["ce"]["exchange_type"] == 2          # NSE -> 2, not 5 (MCX)
    assert leg["ce"]["oi_source"] == "HISTCAP"
    assert leg["ce"]["tradingsymbol"] == "NIFTY10SEP2624000CE"
    assert leg["pe"]["oi_chg"] == -50.0
    assert leg["pe"]["token"] == "112"


def test_autoscalp_chain_mcx_exchange_type(monkeypatch):
    monkeypatch.setattr(runtime.market_hub, "get_chain",
                        lambda sym, **kw: {"chain": [_hc_row(280.0)], "expiry": "25SEP2026"})
    monkeypatch.setattr(runtime, "_merge_broker_greeks", lambda *a, **k: None)
    out = runtime._autoscalp_chain("NATURALGAS", 280, 2, market="MCX")
    assert out[0]["ce"]["exchange_type"] == 5


def test_autoscalp_chain_empty_result_returns_empty_list(monkeypatch):
    monkeypatch.setattr(runtime.market_hub, "get_chain", lambda sym, **kw: {"chain": [], "expiry": None})
    out = runtime._autoscalp_chain("NIFTY", 24000, 2)
    assert out == []


def test_autoscalp_chain_exception_from_get_chain_returns_empty_list_not_raise(monkeypatch):
    def _boom(sym, **kw):
        raise RuntimeError("broker down")
    monkeypatch.setattr(runtime.market_hub, "get_chain", _boom)
    out = runtime._autoscalp_chain("NIFTY", 24000, 2)
    assert out == []


def test_autoscalp_chain_triggers_greeks_merge_when_histcap_marks_unavailable(monkeypatch):
    """Regression guard: histcap ALWAYS tags greeks_source='UNAVAILABLE' (never
    None/''), so the merge-fallback trigger must include that value too, or
    greeks silently never get filled in once histcap starts answering most
    cycles (a real gap found and fixed while doing this unification)."""
    monkeypatch.setattr(runtime.market_hub, "get_chain",
                        lambda sym, **kw: {"chain": [_hc_row(24000.0)], "expiry": "10SEP2026"})
    calls = {"n": 0}
    def _merge(sdk, symbol, expiry, chain_rows):
        calls["n"] += 1
        for row in chain_rows:
            row["ce"]["delta"] = 0.5
            row["ce"]["greeks_source"] = "BROKER"
    monkeypatch.setattr(runtime, "_merge_broker_greeks", _merge)

    class _FakeSDK:
        pass
    import app.connectors.angelone as angelone_mod
    monkeypatch.setattr(angelone_mod, "_market_sdk", lambda require_auth=False: _FakeSDK())

    out = runtime._autoscalp_chain("NIFTY", 24000, 2)
    assert calls["n"] == 1, "greeks merge must fire when every leg is tagged UNAVAILABLE"
    assert out[0]["ce"]["delta"] == 0.5


def test_autoscalp_chain_does_not_merge_greeks_when_already_present(monkeypatch):
    row = _hc_row(24000.0, ce_delta=0.55, ce_greeks_source="BROKER",
                 pe_delta=-0.4, pe_greeks_source="BROKER")
    monkeypatch.setattr(runtime.market_hub, "get_chain",
                        lambda sym, **kw: {"chain": [row], "expiry": "10SEP2026"})
    calls = {"n": 0}
    monkeypatch.setattr(runtime, "_merge_broker_greeks",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    runtime._autoscalp_chain("NIFTY", 24000, 2)
    assert calls["n"] == 0, "must not re-fetch greeks when already populated"


def test_autoscalp_chain_passes_expiry_mode_and_window_through(monkeypatch):
    seen = {}
    def _fake_get_chain(sym, *, window, allow_rest_fallback, expiry_mode):
        seen.update(sym=sym, window=window, allow_rest_fallback=allow_rest_fallback,
                    expiry_mode=expiry_mode)
        return {"chain": [], "expiry": None}
    monkeypatch.setattr(runtime.market_hub, "get_chain", _fake_get_chain)
    runtime._autoscalp_chain("NIFTY", 24000, 3, market="NSE", expiry_mode="AUTO_ROLL")
    assert seen == {"sym": "NIFTY", "window": 3, "allow_rest_fallback": True,
                    "expiry_mode": "AUTO_ROLL"}
