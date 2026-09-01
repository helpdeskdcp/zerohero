#!/usr/bin/env bash
# Run Claude Code against the LOCAL Qwen3-4B model (llama.cpp + proxy).
#
# SAFETY: this only sets env vars for THIS process + uses a separate
# CLAUDE_CONFIG_DIR. Your real ~/.claude config and OAuth login are untouched,
# and NO Anthropic API tokens are consumed (all traffic -> 127.0.0.1).
#
# Prereqs (start these first, in order):
#   CTX=8192 KV=f16 /root/llm-local/start-server.sh     # terminal 1
#   /root/llm-local/start-proxy.sh                       # terminal 2
#
# Usage:
#   /root/llm-local/claude-local.sh -p "prompt" --allowedTools Read Edit Bash
#   /root/llm-local/claude-local.sh                      # interactive
#
# NOTE: keep --allowedTools SMALL (Read/Edit/Bash/Grep/Glob). The full 25-tool
# schema set is ~17k tokens and will not fit the 8k context.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

curl -sf http://127.0.0.1:8789/health >/dev/null 2>&1 || {
  echo "!! proxy not up on 127.0.0.1:8789 — run: $DIR/start-proxy.sh" >&2; exit 1; }
curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1 || {
  echo "!! llama.cpp not up on 127.0.0.1:8080 — run: CTX=8192 KV=f16 $DIR/start-server.sh" >&2; exit 1; }

export CLAUDE_CONFIG_DIR="$DIR/cc-local-config"
export ANTHROPIC_BASE_URL="http://127.0.0.1:8789"
export ANTHROPIC_AUTH_TOKEN="local-no-key-needed"
export ANTHROPIC_MODEL="qwen3-4b"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="qwen3-4b"
export ANTHROPIC_DEFAULT_SONNET_MODEL="qwen3-4b"
export ANTHROPIC_DEFAULT_OPUS_MODEL="qwen3-4b"
export ANTHROPIC_SMALL_FAST_MODEL="qwen3-4b"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export DISABLE_TELEMETRY=1 DISABLE_ERROR_REPORTING=1 DISABLE_AUTOUPDATER=1
export API_TIMEOUT_MS=600000

exec claude "$@"
