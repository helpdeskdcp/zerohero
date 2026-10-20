import copy
import os
import sqlite3

import pytest
import yaml

from app.rules_engine import engine as E, expr as X, replay as RP

POL = yaml.safe_load(open(os.path.join(E.HERE, "policy.yaml")))
GOOD = {"exchange": "NFO", "minute_of_day": 600, "is_holiday": False, "feed_connected": True, "feed_age_sec": 3.0, "trades_today": 0, "consecutive_losses": 0, "daily_pnl_pct": 0.0,
        "open_positions": 0, "duplicate_open_position": False, "minutes_since_last_loss": 999.0, "option_spread_pct": 0.5, "premium": 100.0, "spot": 24000.0, "reward_risk": 2.0, "ev_r": 0.3,
        "kill_switch_active": False, "live_orders_requested": False, "evidence_status": "UNVALIDATED"}


# ---------------- expression safety ----------------
@pytest.mark.parametrize("bad", ["__import__('os').system('x')", "a.b", "a[0]", "[x for x in y]", "lambda: 1", "f(1)", "a if b else c", "a @ b", "a is b", "x in y", "{1:2}", "open('f')", "a ** 2", "a // 2"])
def test_expr_rejects_everything_but_the_whitelist(bad):
    with pytest.raises(X.ExprError): X.compile_expr(bad)


def test_expr_semantics():
    f = X.compile_expr("a > 1 and (b in ['x','y'] or not c) and -d < 0 and a + b2 * 2 >= 5")
    assert f({"a": 2, "b": "y", "c": True, "d": 1, "b2": 2}) is True
    assert f({"a": 2, "b": "z", "c": True, "d": 1, "b2": 2}) is False
    with pytest.raises(X.MissingInput): X.compile_expr("a > 1")({})
    with pytest.raises(X.MissingInput): X.compile_expr("a > 1")({"a": None})
    assert X.compile_expr("a and b")({"a": False}) is False                                            # short-circuit: b never needed


# ---------------- policy validation ----------------
def mut(fn):
    p = copy.deepcopy(POL); fn(p); return p


def test_default_policy_loads_and_is_paper_only():
    e = E.Engine(POL); assert e.p["validation_status"] == "DRAFT_UNVALIDATED" and len(e.rules) >= 15


@pytest.mark.parametrize("fn,msg", [
    (lambda p: p["execution"].update(live_orders_enabled=True), "PAPER"),
    (lambda p: p["execution"].update(mode="LIVE"), "PAPER"),
    (lambda p: p.update(rules=[r for r in p["rules"] if r["id"] != "R-LIVE-NEVER"]), "R-LIVE-NEVER"),
    (lambda p: p["rules"].append(dict(p["rules"][0])), "unique"),
    (lambda p: p["rules"][1].update(when="L_nope > 1"), "undefined limit"),
    (lambda p: p["rules"][1].update(when="exec('x')"), "not allowed"),
    (lambda p: p["rules"][1].update(action="ALLOW"), "action"),
    (lambda p: p["limits"].update(max_trades_per_day="six"), "numeric"),
    (lambda p: p["rules"][1].pop("reason"), "reason"),
])
def test_policy_validation_fails_closed(fn, msg):
    with pytest.raises(E.PolicyError, match=msg): E.Engine(mut(fn))


# ---------------- evaluation ----------------
ENG = E.Engine(POL)


def test_clean_context_is_allowed_with_the_honesty_warning():
    d = ENG.evaluate(GOOD)
    assert d.allow and not d.blocked_by and any(w["rule"] == "W-NO-VALIDATED-EDGE" for w in d.warnings)


@pytest.mark.parametrize("rule,change", [
    ("R-LIVE-NEVER", {"live_orders_requested": True}), ("R-KILLSWITCH", {"kill_switch_active": True}), ("R-CALENDAR", {"is_holiday": True}),
    ("R-SESSION-NSE", {"minute_of_day": 901}), ("R-SESSION-NSE", {"minute_of_day": 559}), ("R-SESSION-MCX", {"exchange": "MCX", "minute_of_day": 1381}),
    ("R-FEED-CONNECTED", {"feed_connected": False}), ("R-FEED-FRESH", {"feed_age_sec": 12.1}), ("R-MAX-TRADES", {"trades_today": 6}),
    ("R-CONSEC-LOSSES", {"consecutive_losses": 3}), ("R-DAILY-LOSS", {"daily_pnl_pct": -2.0}), ("R-CONCURRENT", {"open_positions": 3}),
    ("R-DUPLICATE", {"duplicate_open_position": True}), ("R-COOLDOWN", {"minutes_since_last_loss": 14.0}), ("R-SPREAD", {"option_spread_pct": 1.3}),
    ("R-PREMIUM-MIN", {"premium": 7.9}), ("R-PREMIUM-MAX", {"premium": 2000.0}), ("R-RR", {"reward_risk": 1.2}), ("R-EV", {"ev_r": 0.11})])
def test_each_block_rule_blocks_and_only_it(rule, change):
    d = ENG.evaluate({**GOOD, **change}); assert not d.allow and rule in [b["rule"] for b in d.blocked_by], d.blocked_by
    assert ENG.evaluate({**GOOD, **{k: GOOD[k] for k in change}}).allow


def test_boundaries_are_inclusive_where_stated():
    assert ENG.evaluate({**GOOD, "trades_today": 5}).allow and not ENG.evaluate({**GOOD, "trades_today": 6}).allow
    assert ENG.evaluate({**GOOD, "feed_age_sec": 12.0}).allow and ENG.evaluate({**GOOD, "reward_risk": 1.3}).allow


def test_missing_input_blocks_live_but_is_skipped_in_replay():
    ctx = {k: v for k, v in GOOD.items() if k != "feed_age_sec"}
    d = ENG.evaluate(ctx); assert not d.allow and any("unevaluable" in b["reason"] for b in d.blocked_by) and {"rule": "R-FEED-FRESH", "missing": "feed_age_sec"} in d.not_evaluable
    s = ENG.evaluate(ctx, on_missing="skip"); assert s.allow and {"rule": "R-FEED-FRESH", "missing": "feed_age_sec"} in s.not_evaluable


def test_mcx_context_does_not_need_nse_inputs_and_zero_division_fails_closed():
    assert ENG.evaluate({**GOOD, "exchange": "MCX", "minute_of_day": 700}).allow
    pol = mut(lambda p: p["rules"].append({"id": "R-X", "when": "1 / zero > 0", "action": "BLOCK", "reason": "x"}))
    d = E.Engine(pol).evaluate({**GOOD, "zero": 0}); assert not d.allow and "R-X" in [b["rule"] for b in d.blocked_by]


def test_audit_only_when_asked_and_hash_is_stable(tmp_path, monkeypatch):
    path = str(tmp_path / "a.db"); monkeypatch.setattr(E, "AUDIT_DB", path)
    d = ENG.evaluate(GOOD); assert not os.path.exists(path)
    d2 = ENG.evaluate(GOOD, audit=True); assert d.ctx_hash == d2.ctx_hash
    c = sqlite3.connect(path); assert c.execute("select count(*), sum(allow) from decisions").fetchone() == (1, 1)


# ---------------- replay ----------------
def row(i, o, c, pnl, **kw):
    base = {"trade_id": f"t{i}", "opened_ts": f"2026-10-14T{o}:00+00:00", "closed_ts": f"2026-10-14T{c}:00+00:00", "market": "NFO", "underlying": f"U{i}", "option_type": "CE", "direction": "BUY",
            "strategy": "S", "entry": 100.0, "stop_loss": 80.0, "target_1": 140.0, "pnl": pnl}
    base.update(kw); return base


def test_replay_max_trades_sequential_state_and_sums():
    rows = [row(i, f"04:{10 + i * 5:02d}", f"04:{12 + i * 5:02d}", 10.0) for i in range(8)]          # 09:40.. IST, all wins, each closed before the next opens
    r = RP.replay(rows, ENG)
    assert [x["allow"] for x in r["rows"]] == [True] * 6 + [False] * 2 and r["summary"]["blocked_by_rule"] == {"R-MAX-TRADES": 2}
    s = r["summary"]; assert s["all"]["n"] == 8 and s["allowed"]["n"] == 6 and s["blocked"]["n"] == 2 and round(s["all"]["sum"], 6) == round(s["allowed"]["sum"] + s["blocked"]["sum"], 6)


def test_replay_consecutive_losses_cooldown_and_duplicates():
    rows = [row(0, "04:10", "04:12", -5.0), row(1, "04:14", "04:16", -5.0), row(2, "04:20", "04:22", 9.0), row(3, "04:35", "04:37", 9.0)]
    r = RP.replay(rows, ENG)
    assert [x["blocked_by"] for x in r["rows"]] == [[], ["R-COOLDOWN"], ["R-COOLDOWN"], []]                                      # blocked trades do not reset the clock; 23 min after the only counted loss is fine
    rows = [row(0, "04:10", "04:12", -5.0), row(1, "04:30", "04:32", -5.0), row(2, "04:50", "04:52", -5.0), row(3, "05:10", "05:12", 5.0)]
    assert RP.replay(rows, ENG)["rows"][3]["blocked_by"] == ["R-CONSEC-LOSSES"]
    rows = [row(0, "04:10", "04:40", 5.0, underlying="X"), row(1, "04:20", "04:30", 5.0, underlying="X")]
    assert RP.replay(rows, ENG)["rows"][1]["blocked_by"] == ["R-DUPLICATE"]


def test_replay_session_weekend_rr_and_unevaluable_reporting():
    rows = [row(0, "10:30", "10:40", 1.0), row(1, "04:10", "04:12", 1.0, entry=100.0, stop_loss=80.0, target_1=110.0), row(2, "04:30", "04:32", 1.0, entry=5.0)]
    r = RP.replay(rows, ENG); assert r["rows"][0]["blocked_by"] == ["R-SESSION-NSE"] and r["rows"][1]["blocked_by"] == ["R-RR"] and r["rows"][2]["blocked_by"] == ["R-PREMIUM-MIN"]
    assert {"R-FEED-FRESH", "R-SPREAD", "R-EV"} <= set(r["not_evaluable"])
    sat = [row(0, "04:10", "04:12", 1.0, opened_ts="2026-10-17T04:10:00+00:00", closed_ts="2026-10-17T04:12:00+00:00")]                  # 2026-10-17 is a Saturday
    assert RP.replay(sat, ENG)["rows"][0]["blocked_by"] == ["R-CALENDAR"]


def test_ledger_loader_is_read_only(tmp_path):
    p = str(tmp_path / "l.db"); c = sqlite3.connect(p)
    c.execute("CREATE TABLE ai_paper_trades (trade_id TEXT, opened_ts TEXT, closed_ts TEXT, status TEXT, market TEXT, underlying TEXT, option_type TEXT, direction TEXT, strategy TEXT, entry REAL, stop_loss REAL, target_1 REAL, pnl REAL)")
    c.execute("INSERT INTO ai_paper_trades VALUES ('a','2026-10-14T04:10:00+00:00','2026-10-14T04:12:00+00:00','CLOSED','NFO','N','CE','BUY','S',100,80,140,5)")
    c.execute("INSERT INTO ai_paper_trades VALUES ('b','2026-10-14T04:10:00+00:00',NULL,'OPEN','NFO','N','CE','BUY','S',100,80,140,NULL)"); c.commit(); c.close()
    rows = RP.load_ledger(p); assert [r["trade_id"] for r in rows] == ["a"]
    assert [r["trade_id"] for r in RP.load_ledger(p, "S")] == ["a"] and RP.load_ledger(p, "OTHER") == []


def test_engine_modules_have_no_network_exec_or_order_imports():
    d = E.HERE
    for f in ("engine.py", "expr.py", "replay.py"):
        s = open(os.path.join(d, f)).read()
        for bad in ("requests", "urllib", "socket", "subprocess", "eval(", "exec(", "app.execution", "place_order", "os.system"): assert bad not in s.replace("`eval`", ""), (f, bad)


# ---------------- shadow observer ----------------
from app.rules_engine import shadow as SH


def _ctx(**kw): return {**GOOD, **kw}


def test_shadow_disabled_under_pytest_unless_forced(tmp_path, monkeypatch):
    p = str(tmp_path / "s.db")
    assert SH.observe(_ctx(), symbol="NIFTY", side="CE", existing_allow=True, existing_reason=None, path=p) is None and not os.path.exists(p)
    monkeypatch.setenv("RULES_ENGINE_SHADOW", "force")
    assert SH.observe(_ctx(), symbol="NIFTY", side="CE", existing_allow=True, existing_reason=None, path=p)["allow"] is True and os.path.exists(p)
    monkeypatch.setenv("RULES_ENGINE_SHADOW", "0")
    assert SH.observe(_ctx(), symbol="NIFTY", side="PE", existing_allow=True, existing_reason=None, path=str(tmp_path / "off.db")) is None


def test_shadow_records_disagreement_throttles_and_summarizes(tmp_path, monkeypatch):
    monkeypatch.setenv("RULES_ENGINE_SHADOW", "force"); SH._last.clear(); p = str(tmp_path / "s.db")
    bad = _ctx(trades_today=7)
    for i in range(3): SH.observe(bad, symbol="NIFTY", side="CE", existing_allow=True, existing_reason=None, path=p, now=1000.0 + i)        # same key within 60 s -> one row
    SH.observe(bad, symbol="NIFTY", side="CE", existing_allow=True, existing_reason=None, path=p, now=1100.0)                               # after throttle -> second row
    SH.observe(_ctx(), symbol="NIFTY", side="CE", existing_allow=False, existing_reason="stale feed", path=p, now=1000.0)
    s = SH.summary(p)
    assert s["rows"] == 3 and s["existing_allow_engine_block"] == 2 and s["existing_block_engine_allow"] == 1 and s["engine_blocks_by_rule"] == {"R-MAX-TRADES": 2}
    assert SH.summary(str(tmp_path / "none.db"))["rows"] == 0


def test_shadow_never_raises_and_build_ctx_maps_runner_state():
    class SG:
        consecutive_losses = 2
        def _trades_today(self): return 4
        def _daily_realised(self): return -1000.0
    ctx = SH.build_ctx(symbol="NIFTY", sig={"decision": "BUY_CE", "entry": 90.0, "rr": 1.8}, opens=[{}], open_keys={("NIFTY", "CE")}, feed_connected=True, feed_age=3.0, last_price=24000.0,
                       exchange="NFO", safeguards=SG())
    assert ctx["trades_today"] == 4 and ctx["consecutive_losses"] == 2 and ctx["duplicate_open_position"] is True and ctx["premium"] == 90.0 and ctx["reward_risk"] == 1.8
    assert round(ctx["daily_pnl_pct"], 3) == -0.5 and ctx["live_orders_requested"] is False
    SH.observe_entry(None, "NIFTY", {}, [], set(), {}, None, None, None, True, None)                                   # garbage in: must not raise


def test_runner_hook_is_guarded_log_only():
    src = open(os.path.join(os.path.dirname(E.HERE), "autoscalp", "runner.py")).read()
    i = src.index("_rules_shadow.observe_entry"); blk = src[i - 400:i + 200]
    assert "try:" in blk and "except Exception:" in src[i:i + 300] and "return" not in src[i:i + 200].split("except Exception:")[0]
    assert src.index("check_entry(") < i < src.index('_record_block(why)\n            await self._emit("autoscalp_blocked", {"symbol": sym, "reason": why')
