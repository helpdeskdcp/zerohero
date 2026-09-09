#!/usr/bin/env bash
# Safe pytest wrapper.
#
# WHY: history had a manual `mv backend/data/chanakya.db /tmp/bak && pytest && mv
# back` -- and when the run crashed before the `&& mv back`, the live DB was
# stranded in /tmp, the service lazily recreated an empty stub, and the autoscalp
# runner died on `no such table: app_settings`.
#
# The suite already isolates itself (tests/conftest.py points app.db at a temp
# file via TEST_DATABASE_URL / CHANAKYA_DB_PATH -- see test_live_db_untouched.py).
# This wrapper just makes that the one obvious way to run tests and fails loudly
# if the live DB is moved, replaced, or truncated by anything during the run.
#
# Usage:  scripts/run_tests.sh [pytest args...]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2                 # -> backend/

# prefer the project venv, fall back to python3 / python
if [ -x "./venv/bin/python" ]; then PY="./venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then PY="python3"
else PY="python"; fi

LIVE_DB="$PWD/data/chanakya.db"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/chanakya-tests.XXXXXX")"
export TEST_DATABASE_URL="$TMP_DIR/test.db"
export CHANAKYA_DB_PATH="$TMP_DIR/test.db"

before=""
[ -f "$LIVE_DB" ] && before="$(stat -c '%i:%s' "$LIVE_DB" 2>/dev/null || echo '')"

cleanup() {
  rc=$?
  rm -rf "$TMP_DIR" 2>/dev/null || true
  if [ -n "$before" ]; then
    if [ ! -f "$LIVE_DB" ]; then
      echo "FATAL: live DB $LIVE_DB is GONE after the test run -- restore it from backup." >&2
      rc=1
    else
      after="$(stat -c '%i:%s' "$LIVE_DB" 2>/dev/null || echo '')"
      if [ "${before%%:*}" != "${after%%:*}" ]; then
        echo "FATAL: live DB $LIVE_DB was REPLACED (inode ${before%%:*} -> ${after%%:*}) during the run." >&2
        rc=1
      fi
    fi
  fi
  exit $rc
}
trap cleanup EXIT

"$PY" -m pytest "$@"
