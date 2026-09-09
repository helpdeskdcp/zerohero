# llm-local — llama.cpp + Qwen3-4B on this VPS (CPU only)

Local OpenAI-compatible LLM endpoint for Claude Code / IDE / any tool, served from
CPU on this 2-vCPU VPS. No GPU.

> **This is the tracked source copy.** The live install runs at `/root/llm-local/`.
> `llama.cpp/` (build) and `models/*.gguf` are **not** in git — see `.gitignore`
> and `../../backend/LOCAL_AI.md` for how to build/download them. The
> Claude-Code-via-proxy lane (`anthropic_proxy.py`, `start-proxy.sh`,
> `claude-local.sh`) is documented in full in **`backend/LOCAL_AI.md`**.

```
Claude Code / IDE / curl
        │  HTTP (OpenAI-compatible)
        ▼
llama-server        ← llama.cpp/build/bin/llama-server, 127.0.0.1:8080
        │
        ▼
Qwen3-4B  (GGUF, Q4_K_M, 2.5 GB)   ← models/Qwen3-4B-Q4_K_M.gguf
        │
        ▼
CPU  (AMD EPYC 9354P, 2 vCPU, AVX2 + AVX-512)
```

## Layout

| Path | What |
|---|---|
| `llama.cpp/` | source + `build/bin/{llama-server,llama-bench,llama-cli}` |
| `models/Qwen3-4B-Q4_K_M.gguf` | the model (from `Qwen/Qwen3-4B-GGUF` on HF, byte-verified) |
| `start-server.sh` | launch the server (127.0.0.1:8080, 2 threads, 4k ctx) |
| `bench.sh` | raw throughput via `llama-bench` (pp / tg tokens/sec) |
| `chat.sh` | one-shot chat request + server-side timings |

## Run

```bash
# terminal 1 — start the server (foreground; Ctrl-C to stop)
/root/llm-local/start-server.sh
# override defaults with env vars if needed:
#   PORT=9000 THREADS=2 CTX=2048 /root/llm-local/start-server.sh

# terminal 2 — smoke test
/root/llm-local/chat.sh "Write a haiku about a VPS."

# raw speed (server not required)
/root/llm-local/bench.sh
```

## Point Claude Code / an IDE at it

OpenAI-compatible base URL: `http://127.0.0.1:8080/v1`  (any string as API key)

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-4b","messages":[{"role":"user","content":"hi"}]}'
```

For remote access, SSH-tunnel it — do **not** bind to 0.0.0.0:
`ssh -L 8080:127.0.0.1:8080 user@vps`

## Measured performance

See `BENCH.md` (written by the speed check).

## Notes / limits

- **RAM is the bottleneck**, not CPU. VPS has ~4 GB free with other services running;
  the model needs ~2.5 GB resident + KV cache. `--mlock` is intentionally NOT used so
  the kernel can reclaim pages under pressure. Keep `CTX` small (2k–4k).
- Only 2 vCPU → generation is in the low tens of tokens/sec at best. Fine for
  autocomplete / short answers, slow for long generations.
- Qwen3 is a hybrid reasoning model. To skip the `<think>` block for speed, append
  `/no_think` to the prompt or send `"chat_template_kwargs":{"enable_thinking":false}`.
- Update the model: drop another `*.gguf` in `models/` and edit `start-server.sh`.
- Rebuild llama.cpp: `cd llama.cpp && git pull && cmake --build build -j2`.
