"""Per-strategy realised-edge metrics (research.aggregate)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.research import _strategy_stats


def _tr(pnl, mfe, mae, status="CLOSED", setup="X"):
    return {"status": status, "pnl": pnl, "mfe": mfe, "mae": mae, "setup": setup,
            "exit_reason": "TARGET" if pnl > 0 else "STOP",
            "opened_ts": "2026-08-31T04:00:00+00:00", "closed_ts": "2026-08-31T04:20:00+00:00"}


def test_strategy_stats_core_numbers():
    s = _strategy_stats([_tr(12.0, 14, 6), _tr(-4.0, 3, 9), _tr(8.0, 10, 2), _tr(0.0, 1, 1)])
    assert s["closed"] == 4 and s["wins"] == 2 and s["losses"] == 1
    assert s["win_rate_pct"] == 50.0
    assert s["total_realized_pnl"] == 16.0
    assert s["expectancy_per_trade"] == 4.0
    assert s["profit_factor"] == 5.0                       # 20 / 4


def test_strategy_stats_reports_excursions():
    s = _strategy_stats([_tr(12.0, 14, 6), _tr(-4.0, 4, 10)])
    assert s["avg_mfe"] == 9.0                             # (14 + 4) / 2
    assert s["avg_mae"] == 8.0                             # (6 + 10) / 2


def test_strategy_stats_all_flat_is_zero_not_dash():
    # the retired-on-disarm case: closed rows exist, all pnl == 0
    s = _strategy_stats([_tr(0.0, 2, 1), _tr(0.0, 1, 3)])
    assert s["closed"] == 2 and s["wins"] == 0 and s["losses"] == 0
    assert s["win_rate_pct"] == 0.0 and s["total_realized_pnl"] == 0.0
    assert s["expectancy_per_trade"] == 0.0
    assert s["profit_factor"] is None                      # no losses -> undefined, not 0
    assert s["avg_mfe"] == 1.5 and s["avg_mae"] == 2.0     # excursions still populate


def test_strategy_stats_empty_bucket():
    assert _strategy_stats([]) == {"closed": 0}
    assert _strategy_stats([{"status": "OPEN", "pnl": 5}]) == {"closed": 0}
