# Speed check — Qwen3-4B Q4_K_M on this VPS (CPU)

**Host:** AMD EPYC 9354P, **2 vCPU** (1 thread/core), AVX2+AVX-512. Debian 12.
**Model:** `Qwen3-4B-Q4_K_M.gguf` (2.32 GiB, 4.02 B params), llama.cpp `458681e` (ggml 0.22.0), native `-march` build.
**Caveat:** the box also runs the `chanakya` trading app on the same 2 vCPUs, so numbers vary run-to-run (load avg was ~2.8 during the test).

## Raw throughput — `llama-bench` (t=2, r=3)

| phase | test | tokens/sec |
|---|---|---|
| prompt / prefill | pp128 | 15.4 ± 6.9 |
| prompt / prefill | pp512 | 18.1 ± 3.3 |
| generation | tg64  | 6.4 ± 3.3 |
| generation | tg128 | 6.8 ± 2.3 |

## End-to-end — `llama-server` HTTP (`/v1/chat/completions`, 4 threads req, 4k ctx)

| request | prompt t/s | gen t/s | TTFT | wall |
|---|---|---|---|---|
| "what is a VPS?" `/no_think`, 38 tok | — (cold) | 4.1 | 4.5 s | 13.6 s |
| reverse-proxy explainer `/no_think`, 57 tok | 19.8 | 9.5 | 1.1 s | 7.0 s |
| Fibonacci fn `/no_think`, 200 tok | 14.5 | 5.0 | 0.8 s | 40.5 s |
| Fibonacci fn, **thinking ON**, 400 tok | 13.3 | 6.1 | 1.1 s | **67 s** (all 400 tok spent inside `<think>`) |

## Takeaways

- **Generation ≈ 5–7 tokens/sec**, prompt processing ≈ 15–20 tokens/sec. First request after
  idle is slower (cold caches).
- **TTFT ≈ 1 s** once warm — fine for short answers / autocomplete-style use.
- A 200-token answer takes ~35–45 s; a 400-token one ~60–70 s. Long generations are painful.
- **Turn off Qwen3 thinking** (`/no_think` in the prompt, or
  `"chat_template_kwargs":{"enable_thinking":false}`) — with it on, the model burns the whole
  token budget reasoning and you wait 2–3× longer for the same visible answer.
- **RAM:** with the server up, ~3.6 GB RSS and only ~1.7 GB free on the box. Usable, not
  comfortable alongside `chanakya`. Don't run it 24/7 during market hours; start it when needed.
- 2 vCPU is the ceiling. More threads won't help (there are only 2 cores). A smaller quant
  (Q3_K_M) or the 1.7B model would roughly double gen speed if that matters more than quality.
