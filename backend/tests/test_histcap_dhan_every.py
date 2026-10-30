from app.histcap import worker as W


def _w():
    return W.CaptureWorker.__new__(W.CaptureWorker)                      # only _dhan_due is used: no store, no threads


def test_default_captures_dhan_every_cycle(monkeypatch):
    monkeypatch.delenv("CHANAKYA_HIST_DHAN_EVERY", raising=False); w = _w()
    assert [w._dhan_due() for _ in range(5)] == [True] * 5


def test_every_sixth_cycle_when_configured(monkeypatch):
    monkeypatch.setenv("CHANAKYA_HIST_DHAN_EVERY", "6"); w = _w()
    got = [w._dhan_due() for _ in range(13)]
    assert [i for i, g in enumerate(got) if g] == [0, 6, 12]               # the first cycle still captures (no 2-minute wait after a start)


def test_bad_value_falls_back_to_every_cycle(monkeypatch):
    monkeypatch.setenv("CHANAKYA_HIST_DHAN_EVERY", "abc"); w = _w()
    assert all(w._dhan_due() for _ in range(3))
