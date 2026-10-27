"""MCX capture also records the NEXT-month future (NG 20-Nov options sit on the 24-Nov future; on the front future's expiry day it is the only live anchor)."""
from app.histcap.worker import CaptureWorker


class _Sdk:
    rows = [{"symbol": "NATURALGAS27OCT26FUT", "expiry": "27OCT2026", "instrumenttype": "FUTCOM"},
            {"symbol": "NATURALGAS24NOV26FUT", "expiry": "24NOV2026", "instrumenttype": "FUTCOM"},
            {"symbol": "NATURALGAS28DEC26FUT", "expiry": "28DEC2026", "instrumenttype": "FUTCOM"},
            {"symbol": "NATURALGAS20NOV26315CE", "expiry": "20NOV2026", "instrumenttype": "OPTFUT"}]
    tok = {"27OCT2026": "1", "24NOV2026": "2", "28DEC2026": "3"}

    def resolve_future_contract(self, sym, expiry):
        if expiry == "AUTO": expiry = "27OCT2026"
        t = self.tok.get(expiry)
        return {"status": "OK", "token": t, "expiry": expiry} if t else {"status": "CONTRACT_NOT_FOUND"}

    def search_instruments(self, symbol=None, exchange=None): return list(self.rows)
    def resolve_option_contract(self, *a, **k): return {"status": "UNAVAILABLE"}


def test_mcx_refs_include_next_month_future():
    w = CaptureWorker.__new__(CaptureWorker); w._ref_cache = {}
    refs = w._refs(_Sdk(), "NATURALGAS")
    futs = [(m["expiry"], m["token"]) for m in refs["tokens_meta"] if m["kind"] == "FUTURE"]
    assert futs == [("27OCT2026", "1"), ("24NOV2026", "2")]
    assert refs["fut_meta"]["expiry"] == "27OCT2026"          # candle/anchor behaviour unchanged


def test_mcx_refs_survive_missing_next_month():
    class S(_Sdk):
        rows = _Sdk.rows[:1]
    w = CaptureWorker.__new__(CaptureWorker); w._ref_cache = {}
    refs = w._refs(S(), "NATURALGAS")
    assert [m["expiry"] for m in refs["tokens_meta"] if m["kind"] == "FUTURE"] == ["27OCT2026"]
