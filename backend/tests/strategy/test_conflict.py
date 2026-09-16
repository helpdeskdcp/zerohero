"""
Cases 7, 8, 9 from the spec's required test list:
  7. CE/PE score tie -> NO_TRADE
  8. Raw BUY + bearish strategy -> conflict
  9. Raw SELL + bullish strategy -> conflict
Plus the spec's own worked numeric examples, reproduced directly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy.base_strategy import StrategyResult  # noqa: E402
from app.strategy.conflict import decide_direction, resolve  # noqa: E402
from app.strategy.config import StrategyConfig  # noqa: E402


def _result(direction, score):
    return StrategyResult(strategy_name=f"{direction}_TEST", direction=direction, valid=score >= 65,
                          score=score, confidence=score, entry_allowed=score >= 65)


def test_spec_example_buy_ce78_pe34_is_valid_ce():
    cfg = StrategyConfig()
    d = resolve("BUY", _result("CE", 78), _result("PE", 34), cfg)
    assert d["direction"] == "CE"


def test_spec_example_buy_ce52_pe48_is_no_trade():
    cfg = StrategyConfig()
    d = resolve("BUY", _result("CE", 52), _result("PE", 48), cfg)
    assert d["direction"] == "NO_TRADE"


def test_spec_example_sell_ce30_pe81_is_valid_pe():
    cfg = StrategyConfig()
    d = resolve("SELL", _result("CE", 30), _result("PE", 81), cfg)
    assert d["direction"] == "PE"


def test_spec_example_buy_but_strategy_says_pe80_is_signal_conflict():
    cfg = StrategyConfig()
    d = resolve("BUY", _result("CE", 20), _result("PE", 80), cfg)
    assert d["status"] == "SIGNAL_CONFLICT"
    assert d["direction"] == "NO_TRADE"          # never auto-trades a conflict


def test_ce_pe_score_tie_is_no_trade():
    cfg = StrategyConfig()
    d = decide_direction(_result("CE", 70), _result("PE", 70), cfg)
    assert d["direction"] == "NO_TRADE"


def test_scores_within_margin_of_each_other_is_no_trade():
    """Both clear the threshold but the margin between them is too thin --
    must not force a direction."""
    cfg = StrategyConfig(minimum_threshold=65, minimum_margin=10)
    d = decide_direction(_result("CE", 72), _result("PE", 68), cfg)
    assert d["direction"] == "NO_TRADE"


def test_raw_buy_with_bearish_strategy_is_a_conflict():
    cfg = StrategyConfig()
    d = resolve("BUY", _result("CE", 25), _result("PE", 85), cfg)
    assert d["status"] == "SIGNAL_CONFLICT"


def test_raw_sell_with_bullish_strategy_is_a_conflict():
    cfg = StrategyConfig()
    d = resolve("SELL", _result("CE", 85), _result("PE", 25), cfg)
    assert d["status"] == "SIGNAL_CONFLICT"


def test_conflict_override_config_lets_strategy_win_when_explicitly_enabled():
    cfg = StrategyConfig(allow_conflict_override=True)
    d = resolve("SELL", _result("CE", 85), _result("PE", 25), cfg)
    assert d["status"] == "SIGNAL_CONFLICT_OVERRIDDEN"
    assert d["direction"] == "CE"
