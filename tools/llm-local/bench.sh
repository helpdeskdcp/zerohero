#!/usr/bin/env bash
# Raw CPU throughput for Qwen3-4B Q4_K_M using llama-bench.
# pp = prompt (prefill) tokens/sec, tg = text-generation tokens/sec
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$DIR/llama.cpp/build/bin/llama-bench"
MODEL="$DIR/models/Qwen3-4B-Q4_K_M.gguf"

"$BIN" -m "$MODEL" -t 2 -p 128 -n 64 -r 3 "$@"
