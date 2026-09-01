#!/usr/bin/env bash
# Quick end-to-end latency check against the running server.
# Prints the reply + server-side timing (tokens/sec) from the JSON.
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"
PROMPT="${1:-Explain what a VPS is in 3 sentences.}"

curl -s "http://$HOST:$PORT/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"qwen3-4b\",\"messages\":[{\"role\":\"user\",\"content\":\"$PROMPT\"}],\"max_tokens\":200,\"stream\":false}" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"]); t=d.get("timings",{}); print("\n--- timings ---"); [print(f"{k}: {v}") for k,v in t.items() if "per_second" in k or "_ms" in k]'
