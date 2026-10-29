"""When the stored quotes have no index price (every REST row API_ERROR, ltp NULL) the arena used to say 'no underlying price captured: cannot detect ATM' and show no ladder at all. Fallback: the live WebSocket price."""
import pytest

from app.arena import contracts as CT
from app.histcap.store import HistStore


@pytest.fixture
def empty_db(tmp_path):
    HistStore(str(tmp_path / "m.db")); CT._spot_feed_cache.clear(); return str(tmp_path / "m.db")


def test_no_stored_spot_falls_back_to_the_live_websocket_price(empty_db, monkeypatch):
    import app.mathematical_confluence.context as ctx, app.connectors.angelone as AO
    monkeypatch.setattr(AO, "_market_sdk", lambda require_auth=False: object()); monkeypatch.setattr(ctx, "_feed_spot", lambda sym, sdk: {"SENSEX": 73410.5}.get(sym))
    assert CT.spot_for("SENSEX", None, empty_db) == (73410.5, "WS FEED (shared Angel stream)")
    assert CT.spot_for("NIFTY", None, empty_db) == (None, "NO_SPOT")                                  # the feed has no price for it: the old answer
    assert CT.spot_for("NATURALGAS", None, empty_db) == (None, "NO_SPOT")                              # commodities have no index stream


def test_the_feed_failing_never_breaks_spot_for(empty_db, monkeypatch):
    import app.connectors.angelone as AO
    def boom(require_auth=False): raise RuntimeError("no broker")
    monkeypatch.setattr(AO, "_market_sdk", boom); assert CT.spot_for("SENSEX", None, empty_db) == (None, "NO_SPOT")


def test_a_stored_index_price_still_wins_over_the_feed(tmp_path, monkeypatch):
    import app.mathematical_confluence.context as ctx
    monkeypatch.setattr(ctx, "_feed_spot", lambda sym, sdk: 1.0); CT._spot_feed_cache.clear()
    st = HistStore(str(tmp_path / "m.db")); from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    row = {"received_ts": now, "exch_ts": None, "snap_key": "k1", "instrument_key": "BSE:99919000", "symbol": "SENSEX", "kind": "INDEX", "exchange": "BSE", "token": "99919000", "session_date_ist": "2026-11-03", "ltp": 73000.0, "source": "ANGELONE_QUOTE_FULL", "quote_status": "OK"}
    rid = st.start_run("T", {}, True)
    with st.independent_transaction() as c: st.write_quotes(c, [row], rid, {"issues": [], "rejected": [], "flagged": 0})
    v, why = CT.spot_for("SENSEX", None, str(tmp_path / "m.db")); assert v == 73000.0 and why.startswith("INDEX")
