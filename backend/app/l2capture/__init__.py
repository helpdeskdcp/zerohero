"""
L2 SnapQuote capture -- Angel One WebSocket subscription mode 3.

Research market-data capture (ORDERFLOW_STAGE9_L2_RESEARCH.md section 6b,
route (a)). Off by default; set L2_CAPTURE_ENABLED=1 to arm. Emits no
BUY/SELL/order and touches nothing on the trading / execution / frozen path.
"""
from .worker import L2CaptureWorker

__all__ = ["L2CaptureWorker"]
