from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class DataResponse:
    status: str
    source: str = "ANGELONE"
    exchange: str | None = None
    symbol: str | None = None
    token: str | None = None
    timestamp: str | None = None
    data_age: float | None = None
    data_status: str = "DATA_UNAVAILABLE"
    payload: Any = None
