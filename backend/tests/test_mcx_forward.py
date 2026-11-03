import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.arena import mcx_forward as M

IST = timezone(timedelta(hours=5, minutes=30))


def session(day="2026-11-03", seed=1, turn=True):
    """one synthetic MCX day: 09:00-23:30 one-minute bars with a 2 % drop from 11:00 to 13:00 and a recovery (a U-turn at the low)"""
    idx = pd.date_range(f"{day} 09:00", f"{day} 23:30", freq="1min", tz=IST); n = len(idx); x = np.arange(n); base = 280 + np.zeros(n)
    if turn:
        base = 280 - np.clip((x - 120) / 120, 0, 1) * 6 + np.clip((x - 240) / 120, 0, 1) * 4.5                  # falls 6 points (2.1 %) in 2 h, then recovers 4.5
    c = base + np.random.default_rng(seed).normal(0, 0.1, n); o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.6, "low": np.minimum(o, c) - 0.6, "close": c}, index=idx)


def test_replay_finds_the_u_turn_once_and_is_causal():
    d = session(); tr = M.replay_day(d, 0.006)
    assert tr and tr["side"].startswith("CE") and tr["signal_ist"] > "13:00" and tr["entry_ist"] > tr["signal_ist"]
    cut = d[d.index < pd.Timestamp("2026-11-03 14:30", tz=IST)]                                                    # the same day with everything after 14:30 removed
    tr2 = M.replay_day(pd.concat([cut, cut.iloc[[-1]].assign()]).iloc[:240 + 300], 0.006)
    assert tr2 is None or tr2["signal_ist"] == tr["signal_ist"]                                                   # removing the future never moves an earlier signal


def test_a_flat_day_has_no_trade_and_a_short_day_is_ignored():
    assert M.replay_day(session(turn=False), 0.006) is None and M.replay_day(session().iloc[:100], 0.006) is None


def test_process_records_each_finished_session_once_and_never_an_unfinished_one(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "load_day", lambda day, db=None, symbol=M.SYMBOL: session(day))
    now = datetime(2026, 11, 4, 8, 0, tzinfo=IST)                                                                  # 3-Nov is finished, 4-Nov has not started
    r1 = M.process(now=now, dir_=str(tmp_path)); first = open(tmp_path / "ng_forward_trades.jsonl").read()
    r2 = M.process(now=now, dir_=str(tmp_path)); assert r2["added"] == [] and open(tmp_path / "ng_forward_trades.jsonl").read() == first       # append-only, idempotent
    assert {a["day"] for a in r1["added"]} == {"2026-11-03"} and r1["summary"]["sessions_observed"] == 1
    early = M.process(now=datetime(2026, 11, 4, 20, 0, tzinfo=IST), dir_=str(tmp_path)); assert early["added"] == []                    # 4-Nov 20:00: that session is still running
    late = M.process(now=datetime(2026, 11, 4, 23, 59, tzinfo=IST), dir_=str(tmp_path)); assert {a["day"] for a in late["added"]} == {"2026-11-04"}


def test_summary_states_not_yet_until_100_trades(tmp_path):
    (tmp_path / "ng_forward_trades.jsonl").write_text("\n".join(json.dumps({"day": f"2026-11-{3 + i:02d}", "thrust": 0.01, "net_bp": 5.0 if i % 2 else -2.0, "gross_bp": 9.0}) for i in range(10)) + "\n")
    (tmp_path / "days_done.jsonl").write_text("")
    s = M.summarize(str(tmp_path)); g = s["levels"]["thrust_0.01"]; assert g["trades"] == 10 and g["status"] == "NOT_YET" and g["needed_for_verdict"] == 100 and "ci90_net_bp" in g
