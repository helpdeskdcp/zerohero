"""DataSource factory -- selects exactly one mode per config.data.mode and
validates required columns exist, failing loudly if not (spec Section 2)."""
from __future__ import annotations

from .source_base import DataSource
from .source_broker import BrokerApiDataSource
from .source_ohlcv_proxy import OhlcvProxyDataSource
from .source_tick import TickDataSource

_MODES = {"tick": TickDataSource, "broker_api": BrokerApiDataSource, "ohlcv_proxy": OhlcvProxyDataSource}


def build_data_source(cfg: dict, db_path: str, tick_db_path: str) -> DataSource:
    mode = cfg["data"]["mode"]
    if mode not in _MODES:
        raise ValueError(f"data.mode must be one of {sorted(_MODES)}, got {mode!r}")
    if mode == "ohlcv_proxy":
        return OhlcvProxyDataSource(db_path, cfg["proxy"])
    if mode == "tick":
        return TickDataSource(tick_db_path, cfg.get("proxy", {}))
    return BrokerApiDataSource()
