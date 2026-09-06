#!/usr/bin/env python3
"""
upstox_auth.py -- Upstox OAuth helper + auth health check.  READ-ONLY.

Never prints the API key / secret / access token. Prints only:
  --health      : booleans (configured / token present / token valid / reachable)
  --login-url   : the browser URL to authorise the app (contains client_id +
                  redirect only, NOT the secret)
  --exchange CODE : swap an OAuth ?code= for a token; prints ok/fail only and
                  reminds you to store the token in .env:UPSTOX_ACCESS_TOKEN
                  (this script does NOT write .env).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.connectors import upstox as U


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--login-url", action="store_true")
    ap.add_argument("--exchange", metavar="CODE", default=None)
    a = ap.parse_args()
    if a.login_url:
        print(U.login_url())
        print("\nOpen the URL, sign in, then copy the `code` query param from the redirect and run:")
        print("  venv/bin/python scripts/upstox_auth.py --exchange <CODE>")
        return
    if a.exchange:
        r = U.exchange_code(a.exchange)
        print(json.dumps(r, indent=2))
        if r.get("ok"):
            print("\n>>> Put the access_token into backend/.env as UPSTOX_ACCESS_TOKEN=... then `chmod 600 .env`.")
        return
    # default: health
    print(json.dumps(U.auth_health(), indent=2))


if __name__ == "__main__":
    main()
