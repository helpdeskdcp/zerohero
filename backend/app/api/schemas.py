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
    market: str | None = None
    symbol: str | None = None
    instrument: str | None = None
    exchange: str | None = None
    symboltoken: str | None = None
    interval: str | None = None
    fromdate: str | None = None
    todate: str | None = None
    timeframe: str | None = None
    expiry: str | None = None
    strike: float | None = None
    underlying: str | None = None
    spot: float | None = None
    chain: list | None = None
    candles: list | None = None
    signal_config: dict | None = None
    oi_config: dict | None = None
    account: dict | None = None
    risk_instrument: dict | None = None
    state: dict | None = None
    limits: dict | None = None


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
    market: str | None = None
    aliases: list | None = None


class KillSwitchRequest(BaseModel):
    active: bool
    policy: str | None = None       # MONITOR | FLATTEN
    reason: str | None = "api"


class TrackPositionRequest(BaseModel):
    symbol: str                         # e.g. "NATGASMINI" or a display name
    symboltoken: str | None = None   # Angel One token; resolved from registry if omitted
    exchange: str | None = None
    option_type: str | None = None   # CE | PE | "" for futures/equity
    strike: float | None = None
    expiry: str | None = None
    direction: str
    entry: float
    target: float
    stop: float
    lots: float = 1
    lot_size: float = 1
    trailing_stop: float | None = 0   # 0 = honour the literal stop, no ratchet


class LevelsRequest(BaseModel):
    trade_id: str
    target: float | None = None
    stop: float | None = None
    trailing_stop: float | None = None


class ComboRequest(BaseModel):
    legs: list[str]
    kind: str | None = "STRANGLE"
    target: float | None = None
    stop: float | None = None
    trail: float | None = None


class ComboLevelsRequest(BaseModel):
    combo_id: str
    target: float | None = None
    stop: float | None = None
    trail: float | None = None
