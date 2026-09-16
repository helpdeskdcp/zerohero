"""
Weighted aggregation + the approved CE/PE/NO_TRADE decision rule, including
the monthly-bias ceiling and its explicit override bar.
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy_mtf.mtf_aggregator import compute_aggregate  # noqa: E402
from app.strategy_mtf.mtf_config import MTFConfig  # noqa: E402


def _bar(i, c, start=datetime.datetime(2020, 1, 1, 3, 45), v=1000):
    t = (start + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"t": t, "o": c - 0.1, "h": c + 0.3, "l": c - 0.3, "c": c, "v": v}


def _clean_uptrend(n, step=0.01, start_price=100.0):
    return [_bar(i, start_price + i * step) for i in range(n)]


def _clean_downtrend(n, step=0.01, start_price=5000.0):
    return [_bar(i, start_price - i * step) for i in range(n)]


def _session_gapped_bars(n_days, price_fn, bars_per_day=75,
                         start=datetime.datetime(2018, 1, 1, 3, 45)):
    """Real-shaped data: `bars_per_day` bars per weekday session, weekends
    skipped -- matches the density mtf_config.TIMEFRAME_LOOKBACK_5M_BARS was
    actually sized against (real NSE ~75 bars/session), unlike a fully
    continuous synthetic series which packs far more calendar time per bar
    count than any real dataset ever would."""
    out = []
    day = start
    idx = 0
    trading_days = 0
    while trading_days < n_days:
        if day.weekday() < 5:   # Mon-Fri
            for j in range(bars_per_day):
                t = (day + datetime.timedelta(minutes=5 * j)).strftime("%Y-%m-%dT%H:%M:%SZ")
                c = price_fn(idx)
                out.append({"t": t, "o": c - 0.1, "h": c + 0.3, "l": c - 0.3, "c": c, "v": 1000})
                idx += 1
            trading_days += 1
        day += datetime.timedelta(days=1)
    return out


def test_strong_uptrend_across_all_timeframes_produces_ce():
    bars = _clean_uptrend(20000, step=0.02)   # enough calendar span for daily/weekly, monthly stays insufficient
    r = compute_aggregate(bars, as_of_ts=bars[-1]["t"])
    assert r.direction == "CE"
    assert r.status in ("AGGREGATE_CONFIRMED", "MONTHLY_OVERRIDE")


def test_strong_downtrend_across_all_timeframes_produces_pe():
    bars = _clean_downtrend(20000, step=0.02)
    r = compute_aggregate(bars, as_of_ts=bars[-1]["t"])
    assert r.direction == "PE"


def test_flat_market_is_no_trade_below_threshold():
    bars = [_bar(i, 100.0) for i in range(20000)]
    r = compute_aggregate(bars, as_of_ts=bars[-1]["t"])
    assert r.direction == "NO_TRADE"
    assert r.status == "BELOW_THRESHOLD"


def test_ltf_evidence_against_a_real_monthly_bias_is_blocked_below_override_bar():
    """A long real bullish Monthly bias (real session-gapped data, ~25
    months), with only a brief, weak LTF dip right at the end -- should NOT
    flip to PE just from short-term noise."""
    long_up = _session_gapped_bars(525, lambda i: 100.0 + i * 0.01)   # ~25 trading months
    last_price = long_up[-1]["c"]
    last_ts = datetime.datetime.strptime(long_up[-1]["t"], "%Y-%m-%dT%H:%M:%SZ")
    weak_dip = [{"t": (last_ts + datetime.timedelta(minutes=5 * (j + 1))).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "o": last_price - j * 0.005, "h": last_price - j * 0.005 + 0.3,
                "l": last_price - j * 0.005 - 0.3, "c": last_price - j * 0.005, "v": 1000}
               for j in range(30)]   # tiny, brief dip
    bars = long_up + weak_dip
    r = compute_aggregate(bars, as_of_ts=bars[-1]["t"])
    assert r.monthly_bias == "BULLISH"
    assert r.direction != "PE"   # never flips to PE on weak, brief LTF noise against a real monthly bias


def test_override_bar_is_configurable_and_can_be_disabled():
    cfg_no_override = MTFConfig(monthly_override_threshold=1000.0)   # effectively unreachable
    bars = _clean_uptrend(20000, step=0.02)
    r = compute_aggregate(bars, as_of_ts=bars[-1]["t"], cfg=cfg_no_override)
    assert r.status != "MONTHLY_OVERRIDE"


def test_every_result_exposes_per_timeframe_audit_detail():
    bars = _clean_uptrend(20000, step=0.02)
    r = compute_aggregate(bars, as_of_ts=bars[-1]["t"])
    d = r.to_dict()
    for tf in ("1mo", "1w", "1d", "1h", "30m", "15m", "5m"):
        assert tf in d["per_timeframe"]
        assert "htf_candle_ts" in d["per_timeframe"][tf]
        assert "htf_confirmed" in d["per_timeframe"][tf]
