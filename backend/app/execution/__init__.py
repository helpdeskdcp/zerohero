"""
Order Adapter — live order lifecycle, kept strictly separate from the Signal /
Risk engines.

    Signal Engine -> Confidence Gate -> Risk Engine -> Trade Pre-Arm
        -> OrderManager -> Broker (Paper | Shadow | AngelOne)
        -> immediate local TradeMonitor -> target / SL / trailing / exit
        -> Reconciler (broker order status + broker position = source of truth)

Nothing in here changes signal maths or strategy logic. LIVE order placement is
disabled unless execution_mode == "LIVE" AND env CHANAKYA_ALLOW_LIVE == "1" AND
a non-empty CHANAKYA_LIVE_CONFIRM_TOKEN is configured — otherwise AngelOneBroker raises
LiveDisabled.
"""
from .broker_base import (  # noqa: F401
    BrokerBase, LiveDisabled, OrderReq, OrderAck, OrderStatusResult,
    PositionSnapshot, Side, OrderType, Leg, OStatus,
)
from .order_manager import OrderManager, make_broker  # noqa: F401
