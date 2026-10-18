#!/usr/bin/env bash
# Weekdays 16:40 IST (after the NSE close and the histcap catch-up): append the forward record of the corrected Hybrid MP variant. Read-only.
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a
./venv/bin/python scripts/hybrid_mp_forward_report.py >> data/ops/hybrid_mp_forward_report.log 2>&1
