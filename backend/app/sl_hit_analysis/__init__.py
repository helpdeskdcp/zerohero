"""Stop-Loss Hit Analysis -- shadow, read-only, descriptive.

Groups already-closed paper trades (ai_paper_trades) by underlying / regime /
strategy / setup / hour-of-day to surface where STOP exits cluster, checks
whether stops are being overshot beyond their planned risk distance (mae vs
|entry-stop_loss|), and checks whether stated entry probability tracks the
actual STOP-hit rate. Purely descriptive counts/ratios on real closed trades
-- no p-values, no significance claims, no wiring into any live gate or the
order path. Small buckets (n below MIN_SAMPLE) are reported as-is with their
n shown, never hidden or rounded into a false impression of confidence.
"""
