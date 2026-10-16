"""Hybrid OrderFlow policy layer (2026-10-16). PAPER / SIGNAL-ONLY. Not wired to any live path; live order execution stays disabled.

strategy_policy.json is the source of truth; policy.py validates it and FAILS CLOSED; option_select.py is the pure CE/PE +
ITM/ATM/OTM contract selector driven only by that policy and a validated chain snapshot."""
