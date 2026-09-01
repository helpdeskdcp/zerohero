#!/usr/bin/env bash
# Anthropic-Messages -> OpenAI translation proxy for Claude Code -> llama.cpp.
# Binds 127.0.0.1:8789 only. Logs to /root/llm-local/proxy.log
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export UPSTREAM="${UPSTREAM:-http://127.0.0.1:8080}"   # llama.cpp OpenAI endpoint
export PROXY_HOST="${PROXY_HOST:-127.0.0.1}"
export PROXY_PORT="${PROXY_PORT:-8789}"
export PROXY_NO_THINK="${PROXY_NO_THINK:-1}"           # disable Qwen3 <think> (much faster)
export PROXY_MAX_TOKENS_CAP="${PROXY_MAX_TOKENS_CAP:-1536}"  # cap CC's 32000 request

exec python3 "$DIR/anthropic_proxy.py"
