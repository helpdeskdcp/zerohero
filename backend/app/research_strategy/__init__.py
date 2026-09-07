"""
research_strategy -- READ-ONLY research strategy panels for the frontend.

Nothing here places an order, emits a live signal, or touches the frozen H1/H7
classifier / live trading / entry / SL / target / risk / execution / calibration
/ broker / cron path. Every strategy in this package is a RANGE-SHAPE / proxy
backtest with an explicit NOT-VALIDATED verdict, surfaced in the UI under
Research so the numbers are visible without pretending they are production-ready.
"""
