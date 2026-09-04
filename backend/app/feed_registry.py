"""
Tiny process-global registry for the shared AngelOne WS market feed.

`main.py` publishes `scalp_runner.feed` here at startup; read-only consumers
(mathematical_confluence.context, the ranking scanner, …) pull live index spot
from it via `get_feed()` instead of making their own REST get_quote calls.

Single-process by design — the feed is one in-memory socket (the `--workers 1`
reason). No cross-process story here.
"""
from __future__ import annotations

_FEED = None


def set_feed(feed) -> None:
    global _FEED
    _FEED = feed


def get_feed():
    return _FEED
