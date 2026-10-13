"""G2: provider health truthfulness, failover hysteresis, tick gate integrity (deterministic, injected clocks)."""
import math

import pytest

from app.platform.marketdata import arbiter as AR
from app.platform.marketdata import health as H
from app.platform.marketdata.types import Tick, validate

REQ = ["NSE:1", "NSE:2"]


def facts(now, **kw):
    base = dict(name="ANGELONE", auth_state="VALID", socket_open=True, subscribed=True, last_message_ts=now - 1,
                instrument_last_ts={"NSE:1": now - 1, "NSE:2": now - 2})
    base.update(kw)
    return H.ProviderFacts(**base)


# ------------------------------------------------------------------ health
def test_healthy_requires_auth_subscription_and_fresh_data():
    now = 1000.0
    assert H.evaluate(facts(now), REQ, now).state == H.HEALTHY
    assert "AUTH_EXPIRED" in H.evaluate(facts(now, auth_state="EXPIRED"), REQ, now).reasons          # socket open + fresh data, expired auth
    assert "NOT_SUBSCRIBED" in H.evaluate(facts(now, subscribed=False), REQ, now).reasons
    assert "SOCKET_CLOSED" in H.evaluate(facts(now, socket_open=False), REQ, now).reasons
    h = H.evaluate(facts(now, instrument_last_ts={"NSE:1": now - 99, "NSE:2": now - 99}), REQ, now)
    assert h.state == H.UNHEALTHY and "NO_FRESH_INSTRUMENT_DATA" in h.reasons and h.stale == REQ


def test_open_socket_alone_is_never_healthy():
    now = 1000.0
    f = H.ProviderFacts(name="DHAN", auth_state="UNKNOWN", socket_open=True, subscribed=False)
    h = H.evaluate(f, REQ, now)
    assert h.state == H.UNHEALTHY and not h.healthy


def test_uncertain_auth_is_accepted_only_when_data_proves_it():
    now = 1000.0
    assert H.evaluate(facts(now, auth_state="UNCERTAIN"), REQ, now).state == H.HEALTHY
    stale = facts(now, auth_state="UNCERTAIN", instrument_last_ts={"NSE:1": now - 99, "NSE:2": now})
    h = H.evaluate(stale, REQ, now)
    assert h.state == H.DEGRADED or "AUTH_UNCERTAIN_UNPROVEN_BY_DATA" in h.reasons


def test_partial_freshness_degrades_reconnect_storm_degrades_and_closed_market_is_idle():
    now = 1000.0
    h = H.evaluate(facts(now, instrument_last_ts={"NSE:1": now - 1, "NSE:2": now - 99}), REQ, now)
    assert h.state == H.DEGRADED and h.fresh == ["NSE:1"]
    assert H.evaluate(facts(now, reconnects_in_window=9), REQ, now).state == H.DEGRADED
    assert H.evaluate(facts(now), [], now).state == H.IDLE


def test_adapters_over_real_status_shapes():
    now = 5000.0
    angel = {"connected": True, "socket_open": True, "subscribed": True, "last_msg_age_sec": 0.4,
             "marks": {"99926000": {"age_sec": 0.3}, "99919000": {"age_sec": 2000.0}}, "last_error": None}
    f = H.facts_from_angel_status(angel, now, token_prefix="NSE:")
    assert f.instrument_last_ts["NSE:99926000"] == pytest.approx(now - 0.3)
    assert H.evaluate(f, ["NSE:99926000"], now).state == H.HEALTHY
    assert H.evaluate(f, ["NSE:99919000"], now).state == H.UNHEALTHY
    dhan = {"socket_open": True, "frames": 0, "auth_state": "EXPIRED", "last_msg_age_sec": None, "reconnects": 40}
    assert H.evaluate(H.facts_from_dhan_status(dhan, now, {}), REQ, now).state == H.UNHEALTHY


# ------------------------------------------------------------------ arbiter
def hs(now, a="HEALTHY", d="HEALTHY"):
    def mk(name, st):
        return H.ProviderHealth(name, st, [] if st == "HEALTHY" else ["X"], [], [], now)
    return {"ANGELONE": mk("ANGELONE", a), "DHAN": mk("DHAN", d)}


def run(arb, seq):
    d = None
    for now, a, dh in seq:
        d = arb.update(hs(now, a, dh), now)
    return d


def test_default_is_selected_only_after_confirm_dwell():
    arb = AR.ProviderArbiter()
    assert run(arb, [(0, "HEALTHY", "UNHEALTHY")]).active is None
    d = run(arb, [(10, "HEALTHY", "UNHEALTHY")])
    assert d.active == "ANGELONE" and d.data_available and not d.trading_blocked


def test_unvalidated_dhan_is_never_used_even_when_perfectly_healthy():
    arb = AR.ProviderArbiter()
    run(arb, [(0, "HEALTHY", "HEALTHY"), (10, "HEALTHY", "HEALTHY")])
    d = run(arb, [(20, "UNHEALTHY", "HEALTHY"), (40, "UNHEALTHY", "HEALTHY")])
    assert d.active is None and d.trading_blocked and d.data_available is False
    assert any("not eligible" in r for r in d.reasons)


def test_failover_to_validated_secondary_then_failback_with_hysteresis():
    arb = AR.ProviderArbiter()
    arb.mark_validated("DHAN", "probe 2026-10-13: auth valid, 10/10 instruments fresh, recovery tested")
    run(arb, [(0, "HEALTHY", "HEALTHY"), (10, "HEALTHY", "HEALTHY")])
    assert arb.active == "ANGELONE"
    run(arb, [(11, "UNHEALTHY", "HEALTHY")])
    run(arb, [(15, "UNHEALTHY", "HEALTHY")])
    assert arb.active == "ANGELONE"                                       # 4s of failure < fail_after_sec=5
    d = run(arb, [(17, "UNHEALTHY", "HEALTHY")])                          # 6s >= 5s and DHAN healthy for >= confirm_sec
    assert d.active == "DHAN" and arb.handovers[-1]["from"] == "ANGELONE" and arb.handovers[-1]["to"] == "DHAN"
    # default recovers, but needs recover_after_sec AND min_dwell on DHAN
    run(arb, [(30, "HEALTHY", "HEALTHY"), (60, "HEALTHY", "HEALTHY")])
    assert arb.active == "DHAN"
    d = run(arb, [(200, "HEALTHY", "HEALTHY")])
    assert d.active == "ANGELONE" and arb.handovers[-1]["to"] == "ANGELONE"


def test_no_flapping_on_brief_blips():
    arb = AR.ProviderArbiter()
    arb.mark_validated("DHAN", "probe")
    run(arb, [(0, "HEALTHY", "HEALTHY"), (10, "HEALTHY", "HEALTHY")])
    for t in (11, 12, 13):                                                # 2s blip < fail_after_sec
        run(arb, [(t, "UNHEALTHY", "HEALTHY")])
    run(arb, [(14, "HEALTHY", "HEALTHY")])
    assert arb.active == "ANGELONE" and len(arb.handovers) == 1


def test_validation_requires_evidence():
    with pytest.raises(ValueError):
        AR.ProviderArbiter().mark_validated("DHAN", "")


# ------------------------------------------------------------------ tick gate
def tk(provider="ANGELONE", inst="NSE:1", px=100.0, sts=1000.0, rts=1000.2, **kw):
    return Tick(provider, inst, px, sts, rts, **kw)


def test_validation_rejects_bad_ticks():
    assert validate(tk(px=0)) == "BAD_PRICE" and validate(tk(px=math.nan)) == "BAD_PRICE"
    assert validate(tk(sts=2000.0, rts=1000.0)) == "SOURCE_TS_IN_FUTURE"
    assert validate(tk(sts=1.0, rts=1e7)) == "SOURCE_TS_TOO_OLD"
    assert validate(tk(cum_volume=-1)) == "BAD_CUM_VOLUME" and validate(tk()) is None


def test_gate_only_admits_the_active_provider_and_counts_shadow_ticks():
    active = {"p": "ANGELONE"}
    g = AR.TickGate(lambda: active["p"])
    assert g.accept(tk()).accepted
    r = g.accept(tk(provider="DHAN", sts=1001.0, rts=1001.1))
    assert not r.accepted and r.reason == "NOT_ACTIVE_PROVIDER" and g.shadow_by_provider["DHAN"] == 1
    active["p"] = None
    assert g.accept(tk(sts=1002.0)).reason == "NO_ACTIVE_PROVIDER"


def test_duplicates_late_ticks_and_ordering():
    g = AR.TickGate(lambda: "ANGELONE")
    assert g.accept(tk(sts=1000.0)).accepted
    assert g.accept(tk(sts=1000.0)).reason == "DUPLICATE"
    assert g.accept(tk(sts=1005.0, px=101.0, rts=1005.1)).accepted
    assert g.accept(tk(sts=1004.0, px=100.5, rts=1005.2)).accepted               # within 2s tolerance
    assert g.accept(tk(sts=1001.0, px=99.0, rts=1005.3)).reason == "LATE"
    assert g.counters["ACCEPTED"] == 3


def test_handover_never_double_counts_and_resets_volume_baseline():
    active = {"p": "ANGELONE"}
    g = AR.TickGate(lambda: active["p"])
    assert g.accept(tk(sts=1000.0, cum_volume=500)).accepted
    assert g.accept(tk(sts=1010.0, px=100.2, rts=1010.1, cum_volume=520)).accepted
    active["p"] = "DHAN"
    overlap = g.accept(tk("DHAN", sts=1008.0, px=100.1, rts=1011.0, cum_volume=90000))      # inside the period Angel already covered
    assert not overlap.accepted and overlap.reason == "BEFORE_HANDOVER_BOUNDARY"
    first = g.accept(tk("DHAN", sts=1012.0, px=100.3, rts=1012.1, cum_volume=90100))
    assert first.accepted and first.reset_volume_baseline is True
    nxt = g.accept(tk("DHAN", sts=1013.0, px=100.4, rts=1013.1, cum_volume=90150))
    assert nxt.accepted and nxt.reset_volume_baseline is False
    active["p"] = "ANGELONE"                                                                # failback also resets
    back = g.accept(tk("ANGELONE", sts=1020.0, px=100.5, rts=1020.1, cum_volume=700))
    assert back.accepted and back.reset_volume_baseline is True


def test_price_discontinuity_is_flagged_not_hidden():
    active = {"p": "ANGELONE"}
    g = AR.TickGate(lambda: active["p"], discontinuity_pct=1.0)
    g.accept(tk(sts=1000.0, px=100.0))
    active["p"] = "DHAN"
    r = g.accept(tk("DHAN", sts=1010.0, px=105.0, rts=1010.1))
    assert r.accepted and r.price_discontinuity is True


# ------------------------------------------------------------------ adapters + service
def test_instrument_map_never_guesses_and_rejects_conflicts():
    from app.platform.marketdata.adapters import InstrumentMap, tick_from_angel_tick, tick_from_dhan_packet
    m = InstrumentMap()
    m.register("NSE:NIFTY-IDX", "ANGELONE", (1, "99926000"))
    m.register("NSE:NIFTY-IDX", "DHAN", (0, 13))
    assert m.canonical("DHAN", (0, "13")) == "NSE:NIFTY-IDX" and m.native("NSE:NIFTY-IDX", "ANGELONE") == ("1", "99926000")
    assert m.canonical("DHAN", (0, 999)) is None
    with pytest.raises(ValueError):
        m.register("NSE:OTHER", "DHAN", (0, 13))
    d = tick_from_dhan_packet({"type": "FULL", "exchange_segment": 0, "security_id": 13, "ltp": 22000.5, "ltq": 75, "ltt": 1790877599,
                               "volume": 123456, "oi": 0}, 1790877600.0, m)
    assert (d.provider, d.instrument, d.cum_volume, d.qty, d.source_ts) == ("DHAN", "NSE:NIFTY-IDX", 123456, 75, 1790877599.0)
    a = tick_from_angel_tick({"token": "99926000", "exchange_type": 1, "ltp": 22000.0, "ts_ms": 1790877599500}, 1790877600.0, m)
    assert a.cum_volume is None and a.qty is None and a.source_ts == pytest.approx(1790877599.5)
    assert tick_from_angel_tick({"token": "1", "exchange_type": 9, "ltp": 1.0, "ts_ms": 1}, 1.0, m) is None
    assert tick_from_dhan_packet({"type": "DISCONNECT"}, 1.0, m) is None


def test_service_blocks_trading_until_a_provider_is_healthy_and_flows_ticks_to_candles():
    import datetime as dt
    from app.platform.marketdata.service import MarketDataService
    IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
    t0 = dt.datetime(2026, 10, 13, 10, 0, tzinfo=IST).timestamp()
    svc = MarketDataService()
    assert svc.trading_blocked is True                                              # nothing evaluated yet => blocked
    got = []
    svc.subscribers.append(got.append)
    inst = "NSE:NIFTY-IDX"

    def good(now):
        return {"ANGELONE": H.ProviderFacts("ANGELONE", "VALID", True, True, now - 0.5, {inst: now - 0.5}),
                "DHAN": H.ProviderFacts("DHAN", "EXPIRED", True, False, None, {})}
    for k in (0, 10):
        d = svc.evaluate(good(t0 + k), {"ANGELONE": [inst], "DHAN": [inst]}, t0 + k)
    assert d.active == "ANGELONE" and svc.trading_blocked is False
    assert svc.healths["DHAN"].state == H.UNHEALTHY
    for i in range(70):
        svc.on_tick(Tick("ANGELONE", inst, 22000 + i % 5, t0 + 10 + i + .1, t0 + 10 + i + .15))
    svc.on_tick(Tick("DHAN", inst, 1.0, t0 + 20.1, t0 + 20.2))                       # standby provider ignored
    svc.tick(t0 + 100)
    one_s = [c for c in got if c.tf_sec == 1]
    assert len(one_s) == 70 and all(c.provider == "ANGELONE" for c in one_s) and svc.gate.shadow_by_provider["DHAN"] == 1
    assert any(c.tf_sec == 5 for c in got) and svc.on_tick(None) == [] and svc.dropped_unmapped == 1
    d = svc.evaluate(good(t0 + 500) | {"ANGELONE": H.ProviderFacts("ANGELONE", "VALID", True, True, t0, {inst: t0})},
                     {"ANGELONE": [inst], "DHAN": [inst]}, t0 + 500)
    d = svc.evaluate({"ANGELONE": H.ProviderFacts("ANGELONE", "VALID", True, True, t0, {inst: t0}),
                      "DHAN": H.ProviderFacts("DHAN", "EXPIRED", False, False, None, {})}, {"ANGELONE": [inst], "DHAN": [inst]}, t0 + 510)
    assert d.trading_blocked is True and svc.trading_blocked is True                # both unhealthy => no trading signals/orders
