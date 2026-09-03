"""EXPIRY ZERO TO HERO — OI-change + lead/lag modules (section 4/5)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.expiry_zero_to_hero.oi_change import OIChangeEngine, classify_oi_action
from app.expiry_zero_to_hero.oi_leadlag import OILeadLagAnalyzer
from app.expiry_zero_to_hero import store


def test_oi_change_derives_doi_and_imbalances_without_assuming_sign():
    ce = [(i, 1000 + 10 * i) for i in range(15)]          # CE OI rising slowly
    pe = [(i, 2000 + 40 * i) for i in range(15)]          # PE OI rising faster
    rows = OIChangeEngine().build(ce, pe, minutes_per_step=1.0)
    r10 = rows[10]
    assert r10["oi_src"] == "ACTUAL" and r10["doi_src"] == "DERIVED"
    assert r10["ce_doi_1"] == 10 and r10["pe_doi_1"] == 40
    assert r10["ce_doi_5"] == 50 and r10["pe_doi_5"] == 200
    # imbalance sign is reported, never assumed: PE OI > CE OI -> positive
    assert r10["oi_imbalance"] > 0
    assert r10["doi_imbalance_5"] is not None
    assert rows[0]["ce_doi_5"] is None                    # not enough history


def test_classify_oi_action_standard_quadrants():
    assert classify_oi_action(+100, +5, None)["label"] == "fresh_buying"
    assert classify_oi_action(+100, -5, None)["label"] == "fresh_writing"
    assert classify_oi_action(-100, +5, None)["label"] == "short_covering"
    assert classify_oi_action(-100, -5, None)["label"] == "long_unwinding"
    assert classify_oi_action(None, +5, None)["label"] is None


def test_leadlag_reports_weak_when_signal_is_noise():
    import random
    random.seed(1)
    sig = {i: random.gauss(0, 1) for i in range(120)}
    prem = {i: 100 + random.gauss(0, 0.5) for i in range(120)}
    out = OILeadLagAnalyzer().analyze(signal_by_min=sig, premium_by_min=prem)
    assert out["verdict"] == "STATISTICALLY_WEAK"
    assert abs(out["peak_corr"]) < 0.3


def test_leadlag_detects_a_real_lead():
    # signal at T strongly predicts premium 3 min later
    prem = {i: 100.0 for i in range(60)}
    sig = {}
    for i in range(57):
        s = 1.0 if i % 6 < 3 else -1.0
        sig[i] = s
        prem[i + 3] = 100.0 * (1 + 0.20 * s)              # +/-20% move 3 min after
    out = OILeadLagAnalyzer().analyze(signal_by_min=sig, premium_by_min=prem, expansion_thr_pct=10)
    assert out["best_directional_accuracy"] >= 0.9
    assert "LEADING" in out["verdict"]


def test_store_write_once_and_dataset_status(tmp_path, monkeypatch):
    monkeypatch.setenv("Z2H_DB_PATH", str(tmp_path / "z.db"))
    import importlib
    importlib.reload(store)
    win = {"meta": {"index": "SENSEX", "expiry": "03SEP2026", "session_date": "2026-09-03",
                    "window": ["14:50", "15:40"], "atm": 76600, "step": 100, "ref_spot": 76599,
                    "n_strikes": 7, "index_bars": 35, "option_bars": 700, "data_notes": {}},
           "index_bars": [], "option_bars": []}
    assert store.save_window(win) is True
    assert store.save_window(win) is False               # write-once
    ds = store.dataset_status()
    assert ds["windows_stored"] == 1
    assert ds["expiry_days_by_index"]["SENSEX"] == ["03SEP2026"]
    assert ds["ready_for_coefficient_fit"] is False
