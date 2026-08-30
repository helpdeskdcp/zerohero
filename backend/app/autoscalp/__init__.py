"""Autonomous PAPER scalping runtime (spec-13/15/17).

Consumes the live WS feed, builds multi-timeframe candles, runs the P1-P5
decision engine, opens/monitors/exits PAPER trades, enforces the spec-15 hard
safeguards, persists every decision to the canonical ZeroHero store, and
recalibrates from resolved live outcomes.

LIVE order routing is NOT enabled here -- it remains behind the existing
server-env triple gate + execution/ OrderManager. This package only ever opens
simulated (paper) positions.
"""
