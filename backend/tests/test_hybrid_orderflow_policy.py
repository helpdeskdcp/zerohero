import copy
import json
from datetime import date

import pytest

from app.hybrid_orderflow import option_select as osel
from app.hybrid_orderflow.policy import POLICY_PATH, PolicyError, load_policy, validate

TODAY = date(2026, 10, 16)


def _p():
    return load_policy()


def _row(side, k, exp="2026-10-20", bid=100.0, ask=101.0, vol=5000, oi=50000, delta=None, age=1.0):
    return {"option_type": side, "strike": k, "expiry": exp, "bid": bid, "ask": ask, "volume": vol, "oi": oi, "delta": delta,
            "quote_age_sec": age, "lot_size": 75, "tick_size": 0.05, "symbol": f"NIFTY{k}{side}"}


def _chain(strikes, side, **kw):
    return [_row(side, k, **kw) for k in strikes]


# --- policy: fail closed ------------------------------------------------------------------------------------------------
def test_shipped_policy_loads_and_live_is_disabled():
    p = _p()
    assert p["execution"]["live_orders_enabled"] is False and p["execution"]["mode"] == "PAPER"
    assert p["scoring"]["is_probability"] is False and p["validation_status"] == "DRAFT_UNVALIDATED"


@pytest.mark.parametrize("mutate,needle", [
    (lambda p: p["execution"].__setitem__("live_orders_enabled", True), "live_orders_enabled"),
    (lambda p: p["execution"].__setitem__("mode", "LIVE"), "execution.mode"),
    (lambda p: p.pop("risk"), "missing section"),
    (lambda p: p.__setitem__("policy_version", "2.0.0"), "unsupported policy major"),
    (lambda p: p.__setitem__("policy_version", "one"), "semver"),
    (lambda p: p["data"].__setitem__("require_closed_candle", False), "closed_candle"),
    (lambda p: p["data"].__setitem__("no_forward_fill_missing_bars", False), "forward_fill"),
    (lambda p: p["smc"].__setitem__("bos_requires", "WICK_TOUCH"), "BOS/CHoCH"),
    (lambda p: p["scoring"].__setitem__("is_probability", True), "probability"),
    (lambda p: p["scoring"].__setitem__("min_total_score", 99), "min_total_score"),
    (lambda p: p["options"].__setitem__("preference_order", ["ATM", "OTM"]), "otm_enabled"),
    (lambda p: p["risk"].__setitem__("allow_averaging_down", True), "averaging"),
    (lambda p: p["features"]["delta"].__setitem__("forbidden_classes", []), "OHLC_PROXY"),
    (lambda p: p["no_trade"].remove("KILL_SWITCH"), "KILL_SWITCH"),
])
def test_invalid_or_unsafe_policy_fails_closed(mutate, needle):
    p = copy.deepcopy(_p())
    mutate(p)
    with pytest.raises(PolicyError, match=needle):
        validate(p)


def test_unreadable_policy_fails_closed(tmp_path):
    bad = tmp_path / "p.json"
    bad.write_text("{not json")
    with pytest.raises(PolicyError, match="could not be loaded"):
        load_policy(str(bad))
    with pytest.raises(PolicyError):
        load_policy(str(tmp_path / "missing.json"))


# --- moneyness / ATM ----------------------------------------------------------------------------------------------------
def test_atm_and_itm_otm_boundaries_ce_and_pe():
    listed = [22900, 22950, 23000, 23050, 23100]
    atm = osel.atm_strike(listed, 22997.35)
    assert atm == 23000
    assert osel.classify_moneyness("CE", 22950, 22997.35, atm) == "ITM" and osel.classify_moneyness("CE", 23050, 22997.35, atm) == "OTM"
    assert osel.classify_moneyness("PE", 23050, 22997.35, atm) == "ITM" and osel.classify_moneyness("PE", 22950, 22997.35, atm) == "OTM"
    assert osel.classify_moneyness("CE", 23000, 22997.35, atm) == "ATM" and osel.classify_moneyness("PE", 23000, 22997.35, atm) == "ATM"


def test_atm_tie_break_is_explicit_not_banker_rounding():
    listed = [23000, 23050]
    assert osel.atm_strike(listed, 23025, "LOWER_STRIKE") == 23000       # python round(460.5) would give 460 -> 23000 only by accident of parity
    assert osel.atm_strike(listed, 23025, "HIGHER_STRIKE") == 23050
    assert osel.atm_strike([23050, 23100], 23075, "LOWER_STRIKE") == 23050   # round(461.5)=462 would have given 23100 (strategy/option_selection.py defect)


def test_irregular_and_missing_strikes():
    listed = [22900, 22950, 23050, 23100]                               # 23000 not listed
    assert osel.atm_strike(listed, 22997.35) == 22950                   # 47.35 away vs 52.65
    atm = 22950
    assert osel.band_strike(listed, "CE", "ITM", 22997.35, atm) == 22900 and osel.band_strike(listed, "PE", "ITM", 22997.35, atm) == 23050
    assert osel.band_strike(listed, "CE", "ITM", 22997.35, 22900) is None   # no listed strike beyond the edge


# --- selection examples (also the deliverable's actual CE/PE examples) ----------------------------------------------------
def test_bullish_selects_ce_atm_first():
    r = osel.select_contract("BULLISH", 22997.35, 1.0, _chain([22950, 23000, 23050], "CE") + _chain([22950, 23000, 23050], "PE"), TODAY, _p())
    assert r["status"] == "SELECTED" and r["side"] == "CE" and r["contract"]["strike"] == 23000 and r["moneyness"] == "ATM"


def test_bearish_selects_pe_and_falls_back_to_itm_when_atm_illiquid():
    chain = _chain([22950, 23000, 23050], "PE")
    chain[1]["volume"] = 1                                              # ATM 23000 PE illiquid
    r = osel.select_contract("BEARISH", 22997.35, 1.0, chain, TODAY, _p())
    assert r["status"] == "SELECTED" and r["side"] == "PE" and r["band"] == "ITM" and r["contract"]["strike"] == 23050
    assert r["moneyness"] == "ITM" and r["rejected"][0]["reasons"] == ["ILLIQUID"]


def test_otm_never_selected_by_default_even_if_cheap():
    chain = _chain([22950, 23000, 23050], "CE")
    for c in chain[:2]:
        c["bid"] = c["ask"] = 0                                         # ATM + ITM unusable, cheap OTM 23050 is fine
    r = osel.select_contract("BULLISH", 22997.35, 1.0, chain, TODAY, _p())
    assert r["status"] == "NO_TRADE" and "NO_ELIGIBLE_CONTRACT" in r["reasons"]


def test_expired_contracts_rejected_and_nearest_valid_expiry_used():
    chain = _chain([23000], "CE", exp="2026-10-13") + _chain([23000], "CE", exp="2026-10-27")
    r = osel.select_contract("BULLISH", 23000.0, 1.0, chain, TODAY, _p())
    assert r["status"] == "SELECTED" and r["expiry"] == "2026-10-27"
    only_expired = osel.select_contract("BULLISH", 23000.0, 1.0, _chain([23000], "CE", exp="2026-10-13"), TODAY, _p())
    assert only_expired["status"] == "NO_TRADE" and only_expired["reasons"] == ["CONTRACT_EXPIRED"]


@pytest.mark.parametrize("kw,reason", [({"bid": 101.0, "ask": 100.0}, "CROSSED_OR_LOCKED_BOOK"), ({"bid": 100.0, "ask": 100.0}, "CROSSED_OR_LOCKED_BOOK"),
                                       ({"bid": 100.0, "ask": 110.0}, "SPREAD_TOO_WIDE"), ({"age": 60.0}, "DATA_STALE"),
                                       ({"bid": 0.0, "ask": 0.0}, "DATA_MISSING"), ({"delta": 0.95}, "DELTA_OUT_OF_BAND")])
def test_bad_quotes_are_rejected_with_reason(kw, reason):
    r = osel.select_contract("BULLISH", 23000.0, 1.0, _chain([23000], "CE", **kw), TODAY, _p())
    assert r["status"] == "NO_TRADE" and any(reason in x["reasons"] for x in r["rejected"])


def test_stale_underlying_or_unclear_direction_is_no_trade():
    ch = _chain([23000], "CE")
    assert osel.select_contract("BULLISH", 23000.0, 99.0, ch, TODAY, _p())["reasons"] == ["DATA_STALE"]
    assert osel.select_contract("NEUTRAL", 23000.0, 1.0, ch, TODAY, _p())["reasons"] == ["CONFLICTING_EVIDENCE"]


def test_policy_file_is_valid_json_on_disk():
    json.load(open(POLICY_PATH))
