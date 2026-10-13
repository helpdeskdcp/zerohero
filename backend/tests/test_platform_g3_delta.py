"""Delta protocol hub + frontend client contract (node test run as part of the suite)."""
import os
import shutil
import subprocess

import pytest

from app.platform.candles.engine_1s import Candle
from app.platform.ws.delta_protocol import DeltaHub, candle_json

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def cd(start, tf=60, o=100.0, closed=True, inst="NSE:1", vol=None):
    return Candle(inst, tf, start, "ANGELONE", o, o + 1, o - 1, o + .5, 3, volume=vol, volume_complete=vol is not None,
                  oi=7.0, oi_change=1.0, closed=closed)


def test_candle_json_shifts_display_time_keeps_true_epoch_and_omits_unknown_volume():
    j = candle_json(cd(1_800_000_000))
    assert j["ts"] == 1_800_000_000 and j["time"] == 1_800_000_000 + 19800 and "volume" not in j and j["oi"] == 7.0
    assert candle_json(cd(1, vol=12.0))["volume"] == 12.0


def test_subscribe_gets_snapshot_then_incremental_deltas_with_contiguous_seq():
    h = DeltaHub()
    for i in range(3):
        h.publish(cd(1000 + 60 * i))
    s = h.subscribe("NSE:1", 60)
    first = s.drain()
    assert first[0]["t"] == "snapshot" and first[0]["seq"] == 3 and len(first[0]["candles"]) == 3
    h.publish(cd(1180, closed=False)); h.publish(cd(1180, o=101, closed=True))
    d = s.drain()
    assert [m["seq"] for m in d] == [4, 5] and [m["closed"] for m in d] == [False, True]
    assert len(h.snapshot_message("NSE:1", 60)["candles"]) == 4                     # partial replaced in place, not duplicated


def test_subscribers_only_get_their_instrument_and_timeframe():
    h = DeltaHub()
    a, b = h.subscribe("NSE:1", 60), h.subscribe("NSE:2", 60)
    c5 = h.subscribe("NSE:1", 5)
    a.drain(); b.drain(); c5.drain()
    h.publish(cd(1000, inst="NSE:1"))
    assert len(a.drain()) == 1 and b.drain() == [] and c5.drain() == []


def test_slow_client_is_converted_to_a_resync_and_cannot_block_others():
    h = DeltaHub(max_queue=5)
    slow, fast = h.subscribe("NSE:1", 60), h.subscribe("NSE:1", 60)
    slow.drain()
    for i in range(30):
        h.publish(cd(1000 + 60 * i))
        fast.drain()                                                              # fast client keeps up
    msgs = slow.drain()
    assert slow.resyncs >= 1 and msgs[0]["t"] == "snapshot" and msgs[0]["seq"] <= 30
    assert fast.resyncs == 0
    slow.request_resync()
    assert slow.drain()[0]["seq"] == 30


def test_ring_buffer_is_bounded():
    h = DeltaHub(history=10)
    for i in range(100):
        h.publish(cd(1000 + 60 * i))
    assert len(h.snapshot_message("NSE:1", 60)["candles"]) == 10 and h.status()["streams"] == 1


def test_service_exposes_forming_candles_for_live_charts_without_persisting_them():
    import datetime as dt
    from app.platform.marketdata.service import MarketDataService
    from app.platform.marketdata.types import Tick
    IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
    t0 = dt.datetime(2026, 10, 13, 10, 0, tzinfo=IST).timestamp()
    svc = MarketDataService()
    svc.arbiter.active = "ANGELONE"
    for i in range(7):
        svc.on_tick(Tick("ANGELONE", "NSE:1", 100 + i, t0 + i + .1, t0 + i + .15))
    svc.tick(t0 + 20)
    parts = svc.partials("NSE:1")
    assert parts and all(p.closed is False for p in parts) and {p.tf_sec for p in parts} <= set(svc.multi.tfs)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_frontend_live_chart_client_contract_via_node():
    r = subprocess.run(["node", os.path.join(ROOT, "frontend", "tests", "live_chart.test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all assertions passed" in r.stdout
