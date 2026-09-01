#!/usr/bin/env bash
# llama.cpp server -> Qwen3-4B (GGUF, Q4_K_M) on CPU
# Endpoint: http://127.0.0.1:8080  (OpenAI-compatible: /v1/chat/completions)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$DIR/llama.cpp/build/bin/llama-server"
MODEL="$DIR/models/Qwen3-4B-Q4_K_M.gguf"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"
THREADS="${THREADS:-2}"      # this VPS has 2 vCPU
CTX="${CTX:-4096}"           # keep small; RAM is the bottleneck (~4G free)
KV="${KV:-f16}"              # KV cache type: f16 | q8_0 | q4_0  (quantize to fit bigger CTX)

exec "$BIN" \
  --model "$MODEL" \
  --host "$HOST" --port "$PORT" \
  --threads "$THREADS" --threads-batch "$THREADS" \
  --ctx-size "$CTX" \
  --cache-type-k "$KV" --cache-type-v "$KV" \
  --batch-size 256 --ubatch-size 256 \
  --no-webui \
  "$@"
