"""app.optionchain.qualified_watch -- alerts once per (day, underlying,
direction) on a QUALIFIED verdict, structure-only, no fabricated trade
params. get_chain/analyze/qualify/telegram are stubbed; never calls the
real broker or sends a real Telegram message."""
from app.optionchain import qualified_watch as qw


class _FakeChain:
    def __init__(self, spot=100.0, rows=None):
        self.spot = spot
        self.rows = rows if rows is not None else [object()]


def _qual(verdict, direction):
    return {"verdict": verdict, "direction": direction, "regime_context": "RANGE",
            "structure_bias": {"bias": direction, "net": 2}, "gates": [],
            "summary": f"{verdict} {direction}"}


class _StubQualifyResult:
    def __init__(self, d):
        self._d = d

    def to_dict(self):
        return self._d


def test_qualified_verdict_sends_one_alert(fresh_db, monkeypatch):
    monkeypatch.setattr(qw, "get_chain", lambda sym, exp, allow_network: _FakeChain())
    monkeypatch.setattr(qw, "analyze", lambda chain: object())
    monkeypatch.setattr(qw, "qualify", lambda st: _StubQualifyResult(_qual("QUALIFIED", "LONG")))
    sent = []
    monkeypatch.setattr(qw.telegram_dispatcher, "dispatch",
                         lambda **kw: sent.append((kw["underlying"], kw["direction"])) or {"status": "SENT"})

    out = qw.scan_and_alert(("NIFTY",), today="2026-09-24")
    assert sent == [("NIFTY", "LONG")]
    assert out["alerted"] == ["2026-09-24:NIFTY:LONG"]


def test_watch_verdict_never_alerts(fresh_db, monkeypatch):
    monkeypatch.setattr(qw, "get_chain", lambda sym, exp, allow_network: _FakeChain())
    monkeypatch.setattr(qw, "analyze", lambda chain: object())
    monkeypatch.setattr(qw, "qualify", lambda st: _StubQualifyResult(_qual("WATCH", "LONG")))
    sent = []
    monkeypatch.setattr(qw.telegram_dispatcher, "dispatch",
                         lambda **kw: sent.append(kw) or {"status": "SENT"})

    out = qw.scan_and_alert(("NIFTY",), today="2026-09-24")
    assert sent == []
    assert out["alerted"] == []


def test_second_run_same_day_does_not_double_alert(fresh_db, monkeypatch):
    monkeypatch.setattr(qw, "get_chain", lambda sym, exp, allow_network: _FakeChain())
    monkeypatch.setattr(qw, "analyze", lambda chain: object())
    monkeypatch.setattr(qw, "qualify", lambda st: _StubQualifyResult(_qual("QUALIFIED", "SHORT")))
    sent = []
    monkeypatch.setattr(qw.telegram_dispatcher, "dispatch",
                         lambda **kw: sent.append(kw["underlying"]) or {"status": "SENT"})

    qw.scan_and_alert(("SENSEX",), today="2026-09-24")
    qw.scan_and_alert(("SENSEX",), today="2026-09-24")
    assert sent == ["SENSEX"]  # only the first run alerted


def test_no_data_symbol_is_skipped_not_fatal(fresh_db, monkeypatch):
    monkeypatch.setattr(qw, "get_chain", lambda sym, exp, allow_network: _FakeChain(rows=[]))
    sent = []
    monkeypatch.setattr(qw.telegram_dispatcher, "dispatch",
                         lambda **kw: sent.append(kw) or {"status": "SENT"})

    out = qw.scan_and_alert(("NIFTY",), today="2026-09-24")
    assert sent == []
    assert out["results"]["NIFTY"]["status"] == "NO_DATA"


def test_one_symbol_erroring_does_not_stop_the_rest(fresh_db, monkeypatch):
    def _get_chain(sym, exp, allow_network):
        if sym == "NIFTY":
            raise RuntimeError("boom")
        return _FakeChain()
    monkeypatch.setattr(qw, "get_chain", _get_chain)
    monkeypatch.setattr(qw, "analyze", lambda chain: object())
    monkeypatch.setattr(qw, "qualify", lambda st: _StubQualifyResult(_qual("WATCH", "LONG")))
    monkeypatch.setattr(qw.telegram_dispatcher, "dispatch", lambda **kw: {"status": "SENT"})

    out = qw.scan_and_alert(("NIFTY", "SENSEX"), today="2026-09-24")
    assert out["results"]["NIFTY"]["status"] == "ERROR"
    assert out["results"]["SENSEX"]["verdict"] == "WATCH"
