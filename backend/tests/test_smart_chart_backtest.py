import pytest

from app.smart_chart import backtest, candles, paper, runner, service, store, symbols, templates
from tests import smart_chart_fakes as fk

CFG = {"initial_capital": 1000.0, "commission_type": "percent", "commission_value": 0}


def cdl(closes):
    return [{"time": i * 300_000, "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 0} for i, c in enumerate(closes)]


def tr(eb, xb, ep, xp, size, profit, ent_t=None):
    return {"entry_bar": eb, "exit_bar": xb, "entry_price": ep, "exit_price": xp, "size": size, "profit": profit,
            "entry_time": ent_t or eb * 300_000, "exit_time": None if xb is None else xb * 300_000, "commission": 0}


def test_equity_curve_hand_computed():
    c = cdl([100, 102, 101, 105, 106, 106])
    eq = backtest.equity_curve(c, [tr(1, 3, 100, 104, 2, 8.0)], [], CFG)
    assert eq == [1000.0, 1004.0, 1002.0, 1008.0, 1008.0, 1008.0]          # unrealized while open, realized (net) from the exit bar on


def test_open_trade_marked_to_market_and_entry_commission_deducted():
    cfg = dict(CFG, commission_value=0.1)                                   # 0.1% of notional
    c = cdl([100, 100, 110])
    eq = backtest.equity_curve(c, [], [dict(tr(0, None, 100, None, 1, None), exit_bar=None)], cfg)
    assert eq[0] == pytest.approx(1000 - 0.1) and eq[2] == pytest.approx(1000 + 10 - 0.1)


def test_stats_win_rate_profit_factor_drawdown_and_small_sample_warning():
    c = cdl([100] * 12)
    trades = [tr(0, 1, 100, 110, 1, 10.0), tr(2, 3, 100, 95, 1, -5.0), tr(4, 5, 100, 98, 1, -2.0), tr(6, 7, 100, 106, 1, 6.0)]
    st, eq = backtest.compute_stats(c, trades, [], CFG, {"netprofit": 9.0})
    assert st["trades"] == 4 and st["wins"] == 2 and st["losses"] == 2 and st["win_rate_pct"] == 50.0
    assert st["net_profit"] == 9.0 and st["gross_profit"] == 16.0 and st["gross_loss"] == 7.0 and st["profit_factor"] == pytest.approx(2.286, abs=1e-3)
    assert st["longest_losing_streak"] == 2 and st["reconciliation"]["ok"] is True
    assert any("only 4 closed trades" in w for w in st["warnings"])
    assert st["max_drawdown"] == pytest.approx(7.0)


def test_profit_factor_is_none_without_losses_and_reconciliation_mismatch_is_flagged():
    st, _ = backtest.compute_stats(cdl([100] * 5), [tr(0, 1, 100, 101, 1, 1.0)], [], CFG, {"netprofit": 5.0})
    assert st["profit_factor"] is None and st["reconciliation"]["ok"] is False and any("differs" in w for w in st["warnings"])


def test_causality_diff_detects_changed_past_trades():
    full = {"closed_trades": [tr(5, 8, 100, 103, 1, 3.0)], "open_trades": []}
    same = {"closed_trades": [tr(5, 8, 100, 103, 1, 3.0)], "open_trades": []}
    moved = {"closed_trades": [tr(5, 8, 100, 99, 1, -1.0)], "open_trades": []}
    assert backtest.causality_diff(full, same, 20)["ok"] is True
    assert backtest.causality_diff(full, moved, 20)["ok"] is False
    assert backtest.causality_diff(full, {"closed_trades": [], "open_trades": []}, 20)["ok"] is False
    opened = {"closed_trades": [], "open_trades": [tr(5, None, 100, None, 1, None)]}
    assert backtest.causality_diff(full, opened, 6)["ok"] is True          # at the cut the trade is still open: entry matches, exit not yet comparable
    assert backtest.causality_diff(full, {"closed_trades": [], "open_trades": []}, 6)["ok"] is False   # entry vanished


def test_pick_cuts_bounded_and_after_trades():
    full = {"closed_trades": [tr(40 + 10 * i, 45 + 10 * i, 1, 1, 1, 0) for i in range(30)], "open_trades": []}
    cuts = backtest.pick_cuts(full, 400)
    assert 1 <= len(cuts) <= 6 and all(40 <= c < 400 for c in cuts)


# ------------------------------------------------------------------ integration (offline candles + real PineTS)
pytestmark_node = pytest.mark.skipif(not runner.available()["ok"], reason="node/pinets not installed")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_CHART_DB", str(tmp_path / "sc.db"))
    store.close()
    fk.install(monkeypatch)
    runner._rate.clear()
    yield
    store.close()


NIFTY = symbols.resolve("NIFTY")


@pytestmark_node
def test_backtest_end_to_end_reconciles_and_is_causal(env):
    tpl = next(t for t in templates.TEMPLATES if t["id"] == "ema-cross-strategy")
    s = store.create_script("alice", "ema", "", "strategy", tpl["source"])
    out = service.run_backtest("alice", s["source"], s["id"], s["version"], NIFTY, "5m", 1500, {}, {}, True)
    st = out["stats"]
    assert st["trades"] > 5 and st["reconciliation"]["ok"] is True
    assert st["final_equity"] == pytest.approx(st["initial_capital"] + st["net_profit"] + st["open_pnl"], abs=0.02)      # accounting identity
    assert out["causality"]["ok"] is True and out["causality"]["cuts_tested"] >= 1
    assert len(out["equity"]) == out["range"]["bars"] and out["assumptions"] and out["data_meta"]["source"] == "upstox"
    assert out["data_meta"]["aggregated"] is True and out["data_meta"]["volume_available"] is False
    assert store.get_backtest("alice", out["backtest_id"])["stats"]["trades"] == st["trades"]
    assert store.get_backtest("bob", out["backtest_id"]) is None                   # owner isolation
    # fill model: every entry is at the open of the bar after a signal bar (+/- tick rounding)
    rows = candles.get_candles(NIFTY, "5m", 1500)["candles"]
    by_t = {r["time"]: r for r in rows}
    for t in out["trades"]:
        assert abs(t["entry_price"] - by_t[t["entry_time"]]["open"]) <= 0.026


@pytestmark_node
def test_backtest_rejects_indicator_and_excludes_forming_bar(env, monkeypatch):
    s = store.create_script("alice", "ind", "", "indicator", templates.TEMPLATES[0]["source"])
    with pytest.raises(service.ServiceError) as e:
        service.run_backtest("alice", s["source"], s["id"], 1, NIFTY, "5m", 500, {}, {}, False)
    assert e.value.code == "NOT_A_STRATEGY"
    real = candles.get_candles(NIFTY, "5m", 100)
    forming = {**real, "meta": {**real["meta"], "last_bar_forming": True}}
    monkeypatch.setattr(candles, "get_candles", lambda *a, **k: forming)
    rows, _ = service.load_bars(NIFTY, "5m", 100, closed_only=True)
    assert len(rows) == 99 and rows[-1]["time"] == real["candles"][-2]["time"]


@pytestmark_node
def test_look_ahead_script_is_flagged_by_causality_check(env):
    peek = ('//@version=5\nstrategy("peek", initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=1)\n'
            'h = request.security(syminfo.tickerid, "60", close, lookahead=barmerge.lookahead_on)\n'
            'if close > h[0] and close[1] <= h[1]\n    strategy.entry("L", strategy.long)\nif close < h\n    strategy.close("L")\n')
    s = store.create_script("alice", "peek", "", "strategy", peek)
    out = service.run_backtest("alice", peek, s["id"], 1, NIFTY, "5m", 800, {}, {}, True)
    assert out["causality"]["ok"] is False and any("causality check FAILED" in w for w in out["stats"]["warnings"])


# ------------------------------------------------------------------ paper trading
def fake_strategy(trades_closed, trades_open=()):
    def _run(owner, source, rows, inst, tf, inputs, props, count=True):
        return {"ok": True, "kind": "strategy", "strategy": {"config": {"initial_capital": props["initial_capital"], "commission_type": "percent", "commission_value": props["commission_value"]},
                                                              "closed_trades": [dict(t) for t in trades_closed], "open_trades": [dict(t) for t in trades_open], "pine_summary": {}}}
    return _run


def ptrade(entry_t, exit_t, profit, ep=100.0, side="LONG", eid="L"):
    return {"entry_time": entry_t, "entry_id": eid, "direction": side, "size": 1 if side == "LONG" else -1, "entry_price": ep, "exit_time": exit_t,
            "exit_price": ep + profit, "profit": profit, "commission": 0.0, "entry_bar": 0, "exit_bar": 1}


@pytest.fixture
def portfolio(env, monkeypatch):
    s = store.create_script("alice", "s", "", "strategy", templates.TEMPLATES[3]["source"])
    pf = paper.create("alice", s, NIFTY, "5m", 100000, 0.03, 1, 300)
    paper.start("alice", pf["id"])
    last = candles.get_candles(NIFTY, "5m", 5)["candles"][-1]["time"]
    return pf["id"], pf["start_ms"], last, monkeypatch


def test_paper_idempotent_and_only_trades_after_start(portfolio):
    pid, start, last, mp = portfolio
    t_old = ptrade(start - 10_000_000, start - 9_000_000, 50)                  # before the portfolio existed: never traded
    t1 = ptrade(start + 1_000, start + 2_000, 40)
    t2 = ptrade(start + 3_000, None, 0)
    t2.update(exit_time=None, exit_price=None, profit=None, size=1)
    mp.setattr(service, "_strategy_run", fake_strategy([t_old, t1], [t2]))
    a = paper.step("alice", pid)
    b = paper.step("alice", pid)
    c = paper.step("alice", pid)
    assert a["new_trades"] == 2 and b["new_trades"] == 0 and c["new_trades"] == 0
    tr_ = store.list_paper_trades(pid)
    assert len(tr_) == 2 and sorted(x["status"] for x in tr_) == ["CLOSED", "OPEN"]
    # the open trade later closes: updated in place, not duplicated
    t2c = dict(t2, exit_time=start + 9_000, exit_price=103.0, profit=3.0)
    mp.setattr(service, "_strategy_run", fake_strategy([t_old, t1, t2c]))
    d = paper.step("alice", pid)
    assert d["new_trades"] == 0 and len(store.list_paper_trades(pid)) == 2 and all(x["status"] == "CLOSED" for x in store.list_paper_trades(pid))
    assert d["portfolio"]["state"]["realized"] == pytest.approx(43.0)


def test_paper_daily_loss_limit_skips_entries_and_halts(portfolio):
    pid, start, last, mp = portfolio
    loss = ptrade(start + 1_000, start + 2_000, -400)                          # limit is 300
    later = ptrade(start + 5_000, start + 6_000, 80)                           # same IST day, after the breach -> must be skipped
    mp.setattr(service, "_strategy_run", fake_strategy([loss, later]))
    r = paper.step("alice", pid)
    assert r["new_trades"] == 1 and len(r["skipped"]) == 1 and r["skipped"][0]["reason"] == "DAILY_LOSS_LIMIT"
    assert r["halted"] and store.get_portfolio("alice", pid)["status"] == "HALTED"
    assert paper.step("alice", pid)["advanced"] is False                       # halted portfolios do not advance
    with pytest.raises(service.ServiceError):
        paper.start("alice", pid)
    assert any(e["action"] == "paper.halt" for e in store.list_audit("alice"))


def test_paper_rejects_indicators_and_bad_params_and_isolates_owners(env):
    ind = store.create_script("alice", "i", "", "indicator", templates.TEMPLATES[0]["source"])
    with pytest.raises(service.ServiceError) as e:
        paper.create("alice", ind, NIFTY, "5m", 100000, 0.03, 1, 100)
    assert e.value.code == "NOT_A_STRATEGY"
    st = store.create_script("alice", "s", "", "strategy", templates.TEMPLATES[3]["source"])
    for bad in ((10, 0.03, 1, 0), (100000, 9, 1, 0), (100000, 0.03, -1, 0)):
        with pytest.raises(service.ServiceError):
            paper.create("alice", st, NIFTY, "5m", *bad)
    pf = paper.create("alice", st, NIFTY, "5m", 100000, 0.03, 1, 100)
    assert store.get_portfolio("bob", pf["id"]) is None
    with pytest.raises(service.ServiceError):
        paper.step("bob", pf["id"])
