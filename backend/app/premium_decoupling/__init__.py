"""Premium-vs-Spot Decoupling — shadow, read-only, descriptive.

Classifies the observed relationship between an index's spot move and its
ATM CE/PE premium moves over a time window (e.g. index up but CE premium
also falling = the rally isn't being priced with conviction; both premiums
falling on a flat spot = pure theta bleed). Pure sign-relationship labeling
on already-captured ticks (market_history.db::quote_snapshots) — no new
capture loop, no ML, no backtest claim. Feeds nothing: not wired into any
gate, engine, or order path. See classify.py for the exact rule table.
"""
