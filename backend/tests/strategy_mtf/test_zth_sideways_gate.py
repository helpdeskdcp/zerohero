"""
Sideways/ZTH special case: every gate must hold, none is optional.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy.base_strategy import (
    MarketFeatures,
    build_indicator_snapshot,
)
from app.strategy.config import StrategyConfig
from app.strategy_mtf.fvg_candle import ImbalanceConfirmation
from app.strategy_mtf.mtf_config import MTFConfig
from app.strategy_mtf.zth_sideways_gate import evaluate


def _bar(i, c, h=None, l=None, v=1000):
    return {"t": f"2026-08-04T{3 + i // 12:02d}:{(i * 5) % 60:02d}:00Z",
           "o": c, "h": h if h is not None else c + 0.3, "l": l if l is not None else c - 0.3, "c": c, "v": v}


def _sideways_bars(n=40):
    return [_bar(i, 100.0 + (0.1 if i % 2 == 0 else -0.1)) for i in range(n)]


def _fvg_conf(confirmed=True, direction="BULLISH"):
    return ImbalanceConfirmation(fvg_confirmed=confirmed, fvg_direction=direction if confirmed else None,
                                 inside_candle=False, inside_candle_direction=None, boost=0.6 if confirmed else 0.0)


def _ind_with_expansion(cfg):
    bars = _sideways_bars(39) + [_bar(39, 105.0, h=108.0, l=95.0)]   # a big last-bar expansion
    return build_indicator_snapshot(MarketFeatures(bars=bars), StrategyConfig())


def test_all_gates_pass_allows_the_zth_signal():
    cfg = MTFConfig(sideways_adx_max=90.0, zth_expansion_atr_mult=0.5)   # loosen for a deterministic pass
    ind = _ind_with_expansion(cfg)
    r = evaluate(ind=ind, imbalance=_fvg_conf(True, "BULLISH"), candidate_direction="BULLISH",
                momentum_ok=True, risk_reward=2.0, cfg=cfg)
    assert r.allowed is True and r.direction == "BULLISH"


def test_trending_market_blocks_zth_regardless_of_everything_else():
    cfg = MTFConfig(sideways_adx_max=1.0)   # essentially unreachable -- forces "not sideways"
    ind = _ind_with_expansion(cfg)
    r = evaluate(ind=ind, imbalance=_fvg_conf(True, "BULLISH"), candidate_direction="BULLISH",
                momentum_ok=True, risk_reward=3.0, cfg=cfg)
    assert r.allowed is False and "sideways" in r.reason


def test_no_imbalance_blocks_zth():
    cfg = MTFConfig(sideways_adx_max=90.0, zth_expansion_atr_mult=0.5)
    ind = _ind_with_expansion(cfg)
    r = evaluate(ind=ind, imbalance=_fvg_conf(False), candidate_direction="BULLISH",
                momentum_ok=True, risk_reward=3.0, cfg=cfg)
    assert r.allowed is False


def test_no_expansion_candle_blocks_zth():
    cfg = MTFConfig(sideways_adx_max=90.0, zth_expansion_atr_mult=50.0)   # unreachable expansion bar
    ind = _ind_with_expansion(cfg)
    r = evaluate(ind=ind, imbalance=_fvg_conf(True, "BULLISH"), candidate_direction="BULLISH",
                momentum_ok=True, risk_reward=3.0, cfg=cfg)
    assert r.allowed is False


def test_weak_momentum_blocks_zth():
    cfg = MTFConfig(sideways_adx_max=90.0, zth_expansion_atr_mult=0.5)
    ind = _ind_with_expansion(cfg)
    r = evaluate(ind=ind, imbalance=_fvg_conf(True, "BULLISH"), candidate_direction="BULLISH",
                momentum_ok=False, risk_reward=3.0, cfg=cfg)
    assert r.allowed is False


def test_poor_risk_reward_blocks_zth():
    cfg = MTFConfig(sideways_adx_max=90.0, zth_expansion_atr_mult=0.5)
    ind = _ind_with_expansion(cfg)
    r = evaluate(ind=ind, imbalance=_fvg_conf(True, "BULLISH"), candidate_direction="BULLISH",
                momentum_ok=True, risk_reward=0.5, cfg=cfg, min_rr=1.5)
    assert r.allowed is False


def test_imbalance_direction_disagreeing_with_candidate_blocks_zth():
    cfg = MTFConfig(sideways_adx_max=90.0, zth_expansion_atr_mult=0.5)
    ind = _ind_with_expansion(cfg)
    r = evaluate(ind=ind, imbalance=_fvg_conf(True, "BEARISH"), candidate_direction="BULLISH",
                momentum_ok=True, risk_reward=3.0, cfg=cfg)
    assert r.allowed is False
