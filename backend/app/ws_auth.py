"""WebSocket token check shared by /ws and /ws/platform. Constant-time comparison (a plain `==` leaks how many leading characters matched through response timing). The query-string token form is
kept because existing clients use it; note that a token in a URL can end up in proxy / access logs, so the Basic-auth header form is preferable for new clients."""
from __future__ import annotations

import hmac


def query_token_ok(given: str | None, expected: str | None) -> bool:
    """True only for a non-empty expected token equal to the given one, compared in constant time."""
    if not expected or given is None:
        return False
    return hmac.compare_digest(str(given).encode("utf-8"), str(expected).encode("utf-8"))
