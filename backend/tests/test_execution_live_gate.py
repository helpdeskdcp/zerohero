"""
The triple-gate on AngelOneBroker (app/execution/angelone_broker.py) is the
single most important safety property in this codebase: it must block real
order submission unless mode=='LIVE' AND env CHANAKYA_ALLOW_LIVE=='1' AND a
non-empty CHANAKYA_LIVE_CONFIRM_TOKEN is present. This exercises every
combination and the read-path exemption, with the broker HTTP layer
monkeypatched out entirely (no network).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.execution.broker_base import LiveDisabled, OrderReq  # noqa: E402
from app.execution.angelone_broker import AngelOneBroker       # noqa: E402


def _req():
    return OrderReq(client_tag="T1:ENTRY", trade_id="T1", leg="ENTRY", side="BUY",
                    order_type="MARKET", symbol="NIFTY", symboltoken="123",
                    exchange="NFO", quantity=50, tradingsymbol="NIFTY24SEP25000CE")


def _broker(mode="PAPER", monkeypatch=None, confirm_token=""):
    if monkeypatch is not None:
        if confirm_token:
            monkeypatch.setenv("CHANAKYA_LIVE_CONFIRM_TOKEN", confirm_token)
        else:
            monkeypatch.delenv("CHANAKYA_LIVE_CONFIRM_TOKEN", raising=False)
    return AngelOneBroker(config={"execution_mode": mode})


def test_default_paper_mode_blocks_every_write_path(monkeypatch):
    monkeypatch.delenv("CHANAKYA_ALLOW_LIVE", raising=False)
    b = _broker("PAPER", monkeypatch)
    for fn in (lambda: b.market_entry(_req()), lambda: b.limit_entry(_req()),
              lambda: b.stoploss_market(_req()), lambda: b.stoploss_limit(_req()),
              lambda: b.target_exit(_req()), lambda: b.cancel_order("T1:ENTRY")):
        try:
            fn()
            assert False, "expected LiveDisabled"
        except LiveDisabled:
            pass


def test_live_mode_without_env_flag_still_blocked(monkeypatch):
    monkeypatch.delenv("CHANAKYA_ALLOW_LIVE", raising=False)
    b = _broker("LIVE", monkeypatch, confirm_token="tok")
    assert b.live_enabled is False
    try:
        b.market_entry(_req())
        assert False, "expected LiveDisabled"
    except LiveDisabled as e:
        assert "CHANAKYA_ALLOW_LIVE" in str(e)


def test_live_mode_with_env_flag_but_no_confirm_token_blocked(monkeypatch):
    monkeypatch.setenv("CHANAKYA_ALLOW_LIVE", "1")
    b = _broker("LIVE", monkeypatch, confirm_token="")
    assert b.live_enabled is False
    try:
        b.market_entry(_req())
        assert False, "expected LiveDisabled"
    except LiveDisabled as e:
        assert "CONFIRM_TOKEN" in str(e)
    monkeypatch.delenv("CHANAKYA_ALLOW_LIVE", raising=False)


def test_env_flag_set_to_non_1_value_still_blocked(monkeypatch):
    monkeypatch.setenv("CHANAKYA_ALLOW_LIVE", "true")   # not the literal "1"
    b = _broker("LIVE", monkeypatch, confirm_token="tok")
    assert b.live_enabled is False
    monkeypatch.delenv("CHANAKYA_ALLOW_LIVE", raising=False)


def test_paper_mode_blocked_even_with_both_env_vars_set(monkeypatch):
    """mode is the THIRD independent gate -- env vars alone must never be
    enough while execution_mode stays PAPER."""
    monkeypatch.setenv("CHANAKYA_ALLOW_LIVE", "1")
    b = _broker("PAPER", monkeypatch, confirm_token="tok")
    assert b.live_enabled is False
    try:
        b.market_entry(_req())
        assert False, "expected LiveDisabled"
    except LiveDisabled as e:
        assert "execution_mode=PAPER" in str(e)
    monkeypatch.delenv("CHANAKYA_ALLOW_LIVE", raising=False)


def test_all_three_gates_true_actually_enables_live(monkeypatch):
    monkeypatch.setenv("CHANAKYA_ALLOW_LIVE", "1")
    b = _broker("LIVE", monkeypatch, confirm_token="tok")
    assert b.live_enabled is True
    monkeypatch.delenv("CHANAKYA_ALLOW_LIVE", raising=False)


def test_confirm_token_never_read_from_runtime_config_only_env(monkeypatch):
    """Guards against a future regression where someone wires the confirm
    token through the DB-persisted config dict instead of the process env --
    that would let an API caller flip live trading on remotely."""
    monkeypatch.delenv("CHANAKYA_ALLOW_LIVE", raising=False)
    monkeypatch.delenv("CHANAKYA_LIVE_CONFIRM_TOKEN", raising=False)
    b = AngelOneBroker(config={"execution_mode": "LIVE",
                               "CHANAKYA_LIVE_CONFIRM_TOKEN": "smuggled-in-via-config"})
    assert b.live_enabled is False
    assert b._confirm == ""


def test_read_paths_work_regardless_of_gate(monkeypatch):
    """get_order_status / get_order_book / get_positions must NEVER raise
    LiveDisabled -- SHADOW mode depends on reads working with the gate off."""
    monkeypatch.delenv("CHANAKYA_ALLOW_LIVE", raising=False)
    monkeypatch.delenv("CHANAKYA_LIVE_CONFIRM_TOKEN", raising=False)
    b = _broker("PAPER", monkeypatch)

    import app.connectors.angelone_orders as ao
    monkeypatch.setattr(ao, "find_in_book", lambda **kw: {"status": "OK", "data": {}})
    monkeypatch.setattr(ao, "order_book", lambda: {"status": "OK", "data": []})
    import app.connectors.angelone as ang
    monkeypatch.setattr(ang, "fetch_positions", lambda: {"status": "OK", "positions": []})

    r1 = b.get_order_status(broker_order_id="X")
    r2 = b.get_order_book()
    r3 = b.get_positions()
    assert r1 is not None and r2 == [] and r3.ok is True


def test_submit_calls_guard_before_any_network_call(monkeypatch):
    """The gate must be checked BEFORE the HTTP layer is touched -- patch
    place_order to raise if it's ever reached while blocked."""
    monkeypatch.delenv("CHANAKYA_ALLOW_LIVE", raising=False)
    b = _broker("PAPER", monkeypatch)

    import app.connectors.angelone_orders as ao

    def _boom(*a, **kw):
        raise AssertionError("network layer reached despite blocked gate")
    monkeypatch.setattr(ao, "place_order", _boom)

    try:
        b.market_entry(_req())
        assert False, "expected LiveDisabled"
    except LiveDisabled:
        pass
