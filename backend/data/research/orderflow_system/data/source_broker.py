"""
Mode B -- broker_api DataSource (spec Section 2, Mode B).

NOT IMPLEMENTED, and documented here rather than silently stubbed: in this
codebase, Dhan's real-time WebSocket feed (app/connectors/dhan_ws.py) IS
the tick-level source, exposing genuine 5-level market depth (best bid/ask
at each level) directly -- there is no separate, lower-fidelity "broker
API" path to fall back to that exposes less than the WS feed already does.
Mode A (data/source_tick.py) already reads the real aggressor
classification this feed produces.

If a future broker integration in this codebase only exposes LTP/OHLC via
REST (no depth, no aggressor side), THIS is where that would be wired, with
its exposed fields declared explicitly (spec Section 2, Mode B: "Declare
exactly which fields the API exposes. If aggressor side is not exposed, you
MUST estimate it and mark it 'estimated'.") -- do not silently treat a
declared-limited feed as equivalent to Mode A's real classification.
"""
from __future__ import annotations

import pandas as pd

from .source_base import DataSource


class BrokerApiDataSource(DataSource):
    mode = "broker_api"
    is_proxy = True  # estimated aggressor side, if ever implemented -- never claim "real" here

    def load(self, *, symbol: str, kind: str, timeframe: str, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError(
            "broker_api mode is not implemented in this codebase -- Dhan's WS feed already "
            "exposes real market depth directly (see data/source_tick.py, Mode A), so there is "
            "no separate lower-fidelity broker path to wire here. Use data.mode=tick or "
            "data.mode=ohlcv_proxy instead.")
