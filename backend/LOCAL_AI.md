# LOCAL_AI.md — Claude Code against a local CPU model (Qwen3-4B / llama.cpp)

Status: **proven end-to-end, NOT the default.** A real headless Claude Code
session (v2.1.252) drove the local model through Read → Edit → Bash → answer on
this VPS. It works but is slow (~4 min cold first-token, ~6 min for a trivial
3-tool task). Use it as a fallback / offline / zero-cost lane, not for
interactive work. The normal Anthropic login is untouched and remains the default.

Investigated & built 2026-09-01. Everything lives in `/root/llm-local/`.

---

## 1. Topology

```
Claude Code (native binary 2.1.252)
    │  Anthropic Messages API  (POST /v1/messages, streaming)
    ▼
anthropic_proxy.py        127.0.0.1:8789   (stdlib Python, ~370 lines, no deps)
    │  OpenAI chat-completions  (POST /v1/chat/completions, streaming)
    ▼
llama-server              127.0.0.1:8080   (llama.cpp, CTX=8192, KV=f16)
    ▼
Qwen3-4B-Q4_K_M.gguf      CPU only, 2 vCPU, AVX-512
```

Both ports bind **127.0.0.1 only** — nothing is exposed publicly.

---

## 2. llama.cpp endpoint

| | |
|---|---|
| Binary | `/root/llm-local/llama.cpp/build/bin/llama-server` |
| Model | `/root/llm-local/models/Qwen3-4B-Q4_K_M.gguf` (2.5 GB) |
| URL | `http://127.0.0.1:8080/v1` (OpenAI-compatible) |
| Profile for Claude Code | `CTX=8192 KV=f16` — needed for CC's preamble; ~4.3 GB RSS |
| Default profile (chat/bench) | `CTX=4096 KV=f16` — ~3.2 GB RSS |

Raw speed (`llama-bench`, 2 vCPU): prompt ~15–18 tok/s, generation ~6.5 tok/s.

---

## 3. The proxy

`/root/llm-local/anthropic_proxy.py` — translates Anthropic Messages ⇄ OpenAI
chat-completions. Pure stdlib (`http.server`), no pip installs.

Translates and was **verified** for:

| Feature | Status |
|---|---|
| system prompt (string or blocks) | ✅ → OpenAI `role:system` |
| user / assistant messages | ✅ |
| streaming (Anthropic SSE event sequence) | ✅ `message_start→ping→content_block_*→message_delta→message_stop` |
| tool / function calls | ✅ `tools`+`tool_choice` → OpenAI; `tool_use`/`tool_result` ⇄ `tool_calls`/`role:tool`; streamed `input_json_delta` |
| max_tokens | ✅ (capped, see below) |
| temperature, top_p, stop_sequences | ✅ → `temperature`,`top_p`,`stop` |
| stop conditions | ✅ `end_turn` / `max_tokens` / `tool_use` |
| errors | ✅ Anthropic error shape — 400 `invalid_request_error` (bad JSON), 502 `api_error` (upstream down / context overflow) |
| `POST /v1/messages/count_tokens` | ✅ heuristic stub |

Env knobs (set by `start-proxy.sh`):

| var | default | why |
|---|---|---|
| `UPSTREAM` | `http://127.0.0.1:8080` | llama.cpp endpoint |
| `PROXY_PORT` | `8789` | localhost bind |
| `PROXY_NO_THINK` | `1` | injects `chat_template_kwargs:{enable_thinking:false}` — Qwen3 defaults to `<think>`, which CC never disables; ~2–3× faster without it |
| `PROXY_MAX_TOKENS_CAP` | `1536` | CC hard-requests `max_tokens:32000`; a runaway generation on a 2–3 tok/s CPU model is fatal |

Log: `/root/llm-local/proxy.log` (one line per request: msg count, tool count,
token estimates, TTFT, tok/s, stop reason). **Auto-rotates in-process**
(`RotatingFileHandler`): `PROXY_LOG_MAX_BYTES` (default 2 MB) × `PROXY_LOG_BACKUPS`
(default 3) → `proxy.log` + `.1`/`.2`/`.3`, **~8 MB hard ceiling**, oldest
discarded. No logrotate/cron needed.

---

## 4. Startup / stop

```bash
# START (order matters)
CTX=8192 KV=f16 /root/llm-local/start-server.sh &   # ~15 s to "model loaded"
/root/llm-local/start-proxy.sh &                    # instant
curl -s 127.0.0.1:8080/health 127.0.0.1:8789/health # both {"status":"ok"}

# STOP  (kill by listening port; do NOT `pkill -f` — the pattern self-matches)
for port in 8789 8080; do
  P=$(ss -ltnp | grep ":$port " | grep -oE 'pid=[0-9]+' | cut -d= -f2 | head -1)
  [ -n "$P" ] && kill "$P"
done
```

RAM: the 8k server is ~4.3 GB RSS. This VPS has ~4 GB free with the trading
services (`:8420`, `:7060`) running. **Start on demand, stop when done.** Do not
leave it running during market hours.

---

## 5. Claude Code local-model configuration

Use the wrapper — it sets env for its own process only and points at a
**separate** config dir, so `~/.claude/` and the OAuth login are never touched:

```bash
/root/llm-local/claude-local.sh -p "your task" \
    --allowedTools Read Edit Bash \
    --output-format stream-json --verbose
```

What the wrapper exports (nothing persistent):

```
CLAUDE_CONFIG_DIR=/root/llm-local/cc-local-config   # sandbox: separate history/state
ANTHROPIC_BASE_URL=http://127.0.0.1:8789            # -> proxy
ANTHROPIC_AUTH_TOKEN=local-no-key-needed            # dummy; llama.cpp ignores it
ANTHROPIC_MODEL / _DEFAULT_{HAIKU,SONNET,OPUS}_MODEL / _SMALL_FAST_MODEL = qwen3-4b
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
DISABLE_TELEMETRY=1  DISABLE_ERROR_REPORTING=1  DISABLE_AUTOUPDATER=1
API_TIMEOUT_MS=600000                               # CPU model is slow
```

**You must pass `--allowedTools` with a short list.** Claude Code sends one JSON
schema per enabled tool; the full built-in set (25 tools) is ~14k tokens and will
not fit the 8k context. `Read Edit Bash` (+ optionally `Grep Glob`) → ~3.1k-token
preamble, which fits.

Headless note: `--permission-mode bypassPermissions` /
`--dangerously-skip-permissions` are **refused when running as root**. Rely on
`--allowedTools` instead — listed tools auto-approve in `-p` mode.

---

## 6. LOCAL ↔ ANTHROPIC switching

There is **no switch to throw** — the two are fully separate:

| | Command | Talks to | Config dir | Cost |
|---|---|---|---|---|
| **Anthropic (default)** | `claude` | api.anthropic.com (OAuth) | `~/.claude/` | metered |
| **Local** | `/root/llm-local/claude-local.sh` | `127.0.0.1:8789` → llama.cpp | `/root/llm-local/cc-local-config/` | $0 |

`claude` with no wrapper always uses your normal login. The wrapper never writes
to `~/.claude/`. To "switch" you just choose which command to run.

(If you ever want it session-wide without the wrapper: `export ANTHROPIC_BASE_URL=http://127.0.0.1:8789 ANTHROPIC_AUTH_TOKEN=x` in that shell only, and `unset` them / close the shell to revert. Do **not** put these in `~/.claude/settings.json` or your shell rc.)

---

## 7. Benchmark — real Claude Code session through the proxy

Task: *"read mathutils.py, add `triple(n)`, run `python3 -m unittest -q`, report PASS/FAIL"*
Sandbox: `/root/llm-local/cc-test-project/`. Server `CTX=8192 KV=f16`. Box also running the trading services.

| turn | tool | TTFT | turn total | note |
|---|---|---:|---:|---|
| 1 | `Read` | **240.7 s** | 247.7 s | cold — prefill 3,075-tok preamble @ ~13 tok/s |
| 2 | `Edit` | 27.7 s | 62.4 s | prefix cache hit — only 232 new tokens prefilled |
| 3 | `Bash` (`unittest`) | 18.5 s | 43.1 s | |
| 4 | final answer | 11.7 s | 26.1 s | generation ~2.7 tok/s |
| | **whole task** | | **379.8 s (6.3 min)** | `is_error:false`, 4 turns |

- Token generation under load: **~2–3 tok/s**. Prompt eval: **~13 tok/s**.
- Proxy-only round-trip for a tiny request: TTFT ~0.6–1.5 s.
- **Anthropic API cost: $0.00.** (Claude Code's own `result.total_cost_usd`
  printed `$0.069` — that is its price-book estimate for the model *name*, not
  real spend. No request left localhost.)
- Trading services (`:8420`, `:7060`) stayed up throughout; no OOM.

---

## 8. Limitations (measured, not theoretical)

1. **Too slow for interactive use.** ~4 min to first token on a cold request
   (the ~14k-token tool preamble prefills at ~13 tok/s on 2 vCPU); ~6 min for a
   trivial 3-tool task. Fine for a scripted/offline one-shot, painful live.
2. **Model quality is the real ceiling.** In the test run Qwen3-4B put
   `def triple` *nested inside `add()`* after a `return` (dead, not importable),
   then declared **"PASS"** because the pre-existing tests don't import `triple`.
   The integration did exactly what the model asked — the model was just wrong.
   Do not trust it for anything non-trivial or unsupervised.
3. **Context budget.** Must restrict `--allowedTools` to ~3 tools. The full
   25-tool Claude Code preamble is ~17k tokens → needs a 20k+ ctx server →
   ~3.9 GB RSS → would evict `chanakya`/`zerohero` trading services on this box.
   Not attempted.
4. **RAM.** 8k-ctx server ≈ 4.3 GB RSS; VPS has ~4 GB free. On-demand only.
5. `[claude-code:unrecognized_model] {"model":"qwen3-4b"}` is logged once —
   cosmetic, the session proceeds normally.
6. `--dangerously-skip-permissions` / `bypassPermissions` refused under root;
   use `--allowedTools` allowlisting for headless runs.
7. Vision/image blocks are dropped (text-only model). `thinking` blocks are
   stripped. `web_search`/`web_fetch` server-tools are not supported.
8. Proxy returns context-overflow as HTTP 502 → Claude Code retries it with
   backoff (treats it as transient). Keep the server context large enough.

---

## 9. Rollback

**Nothing persistent was changed — there is nothing to undo in Claude Code.**

- Real `~/.claude/settings.json`, `~/.claude.json`, `~/.claude/.credentials.json`
  were never written. Verified byte-identical.
- The local lane is env-vars + a separate `CLAUDE_CONFIG_DIR` only.

To stand it down:

```bash
# 1. stop the services (by port, see §4)
for port in 8789 8080; do P=$(ss -ltnp|grep ":$port "|grep -oE 'pid=[0-9]+'|cut -d= -f2|head -1); [ -n "$P" ] && kill "$P"; done

# 2. (optional) delete everything
rm -rf /root/llm-local        # server, model, proxy, sandbox, sandbox config

# 3. nothing else. `claude` already uses your normal Anthropic login.
```

To re-verify the normal login still works: just run `claude -p "hi"` (no wrapper).

---

## 10. Files

Tracked source copy: **`tools/llm-local/`** in this repo (scripts, proxy, docs,
sandbox fixtures, `cc-run-evidence.jsonl`). The build (`llama.cpp/`), the model
(`models/*.gguf`) and runtime state (`proxy.log*`, `cc-local-config/`) are
git-ignored — rebuild per §2–§3.

```
/root/llm-local/                    live install (not in git)
  llama.cpp/…/llama-server          built from source
  models/Qwen3-4B-Q4_K_M.gguf
  anthropic_proxy.py                Anthropic<->OpenAI translation proxy (stdlib)
  start-server.sh                   CTX / KV env-configurable
  start-proxy.sh                    PROXY_NO_THINK, PROXY_MAX_TOKENS_CAP
  claude-local.sh                   wrapper: env-only, sandboxed config dir
  cc-local-config/                  sandbox CLAUDE_CONFIG_DIR (not ~/.claude)
  cc-test-project/                  mathutils.py + test — repeatable bench
  proxy.log[.1-.3]                  per-request timing log, in-process rotation (~8 MB cap)
  README.md  BENCH.md               (raw llama.cpp setup + CPU benchmark)
```
