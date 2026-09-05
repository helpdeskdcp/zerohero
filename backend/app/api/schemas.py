"""
Pydantic request models shared across the route modules under app/api/.
Split out of app/main.py so a model used by more than one route group
(KillSwitchRequest by execution + autoscalp, CloseTradeRequest by data +
positions) has exactly one definition instead of being duplicated.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SignalRequest(BaseModel):
    market: Optional[str] = None
    symbol: Optional[str] = None
    instrument: Optional[str] = None
    exchange: Optional[str] = None
    symboltoken: Optional[str] = None
    interval: Optional[str] = None
    fromdate: Optional[str] = None
    todate: Optional[str] = None
    timeframe: Optional[str] = None
    expiry: Optional[str] = None
    strike: Optional[float] = None
    underlying: Optional[str] = None
    spot: Optional[float] = None
    chain: Optional[list] = None
    candles: Optional[list] = None
    signal_config: Optional[dict] = None
    oi_config: Optional[dict] = None
    account: Optional[dict] = None
    risk_instrument: Optional[dict] = None
    state: Optional[dict] = None
    limits: Optional[dict] = None


class CloseTradeRequest(BaseModel):
    trade_id: str
    exit_price: float


class MarkPriceRequest(BaseModel):
    trade_id: str
    ltp: float


class InstrumentRequest(BaseModel):
    name: str
    exchange: str
    symboltoken: str
    market: Optional[str] = None
    aliases: Optional[list] = None


class KillSwitchRequest(BaseModel):
    active: bool
    policy: Optional[str] = None       # MONITOR | FLATTEN
    reason: Optional[str] = "api"


class TrackPositionRequest(BaseModel):
    symbol: str                         # e.g. "NATGASMINI" or a display name
    symboltoken: Optional[str] = None   # Angel One token; resolved from registry if omitted
    exchange: Optional[str] = None
    option_type: Optional[str] = None   # CE | PE | "" for futures/equity
    strike: Optional[float] = None
    expiry: Optional[str] = None
    direction: str
    entry: float
    target: float
    stop: float
    lots: float = 1
    lot_size: float = 1
    trailing_stop: Optional[float] = 0   # 0 = honour the literal stop, no ratchet


class LevelsRequest(BaseModel):
    trade_id: str
    target: Optional[float] = None
    stop: Optional[float] = None
    trailing_stop: Optional[float] = None


class ComboRequest(BaseModel):
    legs: list[str]
    kind: Optional[str] = "STRANGLE"
    target: Optional[float] = None
    stop: Optional[float] = None
    trail: Optional[float] = None


class ComboLevelsRequest(BaseModel):
    combo_id: str
    target: Optional[float] = None
    stop: Optional[float] = None
    trail: Optional[float] = None
