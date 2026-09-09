#!/usr/bin/env python3
"""
Minimal Anthropic Messages API  ->  OpenAI /v1/chat/completions  translation proxy.

Purpose: let Claude Code (which speaks the Anthropic Messages API) talk to a local
llama.cpp server (which speaks OpenAI chat-completions) for a TEST session only.

- stdlib only, no third-party deps
- binds 127.0.0.1 ONLY (never public)
- translates: system prompt, user/assistant/tool messages, tools/function calls,
  max_tokens, temperature, top_p, stop_sequences, streaming (SSE), errors
- logs every request's shape + timing to PROXY_LOG for inspection

Env:
  UPSTREAM   default http://127.0.0.1:8080      (llama.cpp OpenAI endpoint)
  PROXY_HOST default 127.0.0.1
  PROXY_PORT default 8789
  PROXY_LOG  default /root/llm-local/proxy.log   (auto-rotates, see below)
  PROXY_LOG_MAX_BYTES  default 2097152 (2 MB) per file
  PROXY_LOG_BACKUPS    default 3        -> ~8 MB hard ceiling total
  PROXY_MODEL default qwen3-4b   (sent upstream; llama.cpp ignores it anyway)
"""
import json, os, sys, time, http.client, urllib.parse
import logging, logging.handlers
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM   = os.environ.get("UPSTREAM", "http://127.0.0.1:8080")
PROXY_HOST = os.environ.get("PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8789"))
PROXY_LOG  = os.environ.get("PROXY_LOG", "/root/llm-local/proxy.log")
PROXY_LOG_MAX_BYTES = int(os.environ.get("PROXY_LOG_MAX_BYTES", str(2 * 1024 * 1024)))
PROXY_LOG_BACKUPS   = int(os.environ.get("PROXY_LOG_BACKUPS", "3"))
PROXY_MODEL = os.environ.get("PROXY_MODEL", "qwen3-4b")
# Qwen3 defaults to <think> reasoning; Claude Code never sends /no_think.
# PROXY_NO_THINK=1 tells llama.cpp to disable it (much faster on CPU).
PROXY_NO_THINK = os.environ.get("PROXY_NO_THINK", "1") == "1"
# Claude Code hard-requests max_tokens=32000; on a slow CPU model a runaway
# generation is fatal. Cap it (0 = no cap).
PROXY_MAX_TOKENS_CAP = int(os.environ.get("PROXY_MAX_TOKENS_CAP", "1536"))

_up = urllib.parse.urlparse(UPSTREAM)
UP_HOST, UP_PORT = _up.hostname, (_up.port or 80)


def _make_logger():
    lg = logging.getLogger("anthropic_proxy")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    if not lg.handlers:
        try:
            h = logging.handlers.RotatingFileHandler(
                PROXY_LOG, maxBytes=PROXY_LOG_MAX_BYTES,
                backupCount=PROXY_LOG_BACKUPS, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(message)s"))
            lg.addHandler(h)
        except Exception:
            pass
    return lg


_LOG = _make_logger()


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        _LOG.info(line)          # RotatingFileHandler: PROXY_LOG + .1/.2/.3 backups
    except Exception:
        pass


def est_tokens(s):
    """cheap token estimate ~ chars/4"""
    return max(1, len(s) // 4)


# ---------- Anthropic -> OpenAI request ----------

def _text_from_blocks(blocks):
    if isinstance(blocks, str):
        return blocks
    out = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            out.append(b.get("text", ""))
    return "\n".join(out)


def anthropic_to_openai(body):
    msgs_out = []

    # system (string or list of blocks)
    sys_p = body.get("system")
    if sys_p:
        sys_text = _text_from_blocks(sys_p)
        if sys_text.strip():
            msgs_out.append({"role": "system", "content": sys_text})

    for m in body.get("messages", []):
        role = m.get("role")
        content = m.get("content")

        if isinstance(content, str):
            msgs_out.append({"role": role, "content": content})
            continue

        # content is a list of blocks
        text_parts = []
        tool_calls = []
        tool_results = []  # -> emitted as separate role:tool messages (OpenAI)
        for b in content:
            bt = b.get("type")
            if bt == "text":
                text_parts.append(b.get("text", ""))
            elif bt == "thinking":
                pass  # drop reasoning traces on the way in
            elif bt == "tool_use":  # assistant asked to call a tool
                tool_calls.append({
                    "id": b.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": b.get("name", ""),
                        "arguments": json.dumps(b.get("input", {})),
                    },
                })
            elif bt == "tool_result":  # user returning a tool's output
                rc = b.get("content", "")
                if isinstance(rc, list):
                    rc = _text_from_blocks(rc)
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": b.get("tool_use_id", ""),
                    "content": rc if isinstance(rc, str) else json.dumps(rc),
                })
            elif bt == "image":
                text_parts.append("[image omitted: local model is text-only]")

        if role == "assistant":
            a = {"role": "assistant", "content": "\n".join(text_parts)}
            if tool_calls:
                a["tool_calls"] = tool_calls
                if not a["content"]:
                    a["content"] = None
            msgs_out.append(a)
        else:  # user
            if text_parts and any(t.strip() for t in text_parts):
                msgs_out.append({"role": "user", "content": "\n".join(text_parts)})
            msgs_out.extend(tool_results)

    oai = {
        "model": PROXY_MODEL,
        "messages": msgs_out,
        "stream": bool(body.get("stream")),
    }
    if body.get("max_tokens") is not None:
        mt = body["max_tokens"]
        if PROXY_MAX_TOKENS_CAP and mt > PROXY_MAX_TOKENS_CAP:
            mt = PROXY_MAX_TOKENS_CAP
        oai["max_tokens"] = mt
    if PROXY_NO_THINK:
        oai["chat_template_kwargs"] = {"enable_thinking": False}
    if body.get("temperature") is not None:
        oai["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        oai["top_p"] = body["top_p"]
    if body.get("stop_sequences"):
        oai["stop"] = body["stop_sequences"]

    tools = body.get("tools")
    if tools:
        oai_tools = []
        for t in tools:
            # skip Anthropic server-tool specs that have no input_schema
            if "input_schema" not in t and "name" not in t:
                continue
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object"}),
                },
            })
        if oai_tools:
            oai["tools"] = oai_tools
    tc = body.get("tool_choice")
    if tc:
        t = tc.get("type")
        if t == "auto":
            oai["tool_choice"] = "auto"
        elif t == "any":
            oai["tool_choice"] = "required"
        elif t == "tool" and tc.get("name"):
            oai["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
    return oai


STOP_MAP = {"stop": "end_turn", "length": "max_tokens",
            "tool_calls": "tool_use", "content_filter": "end_turn", None: "end_turn"}


# ---------- OpenAI -> Anthropic (non-streaming) ----------

def openai_to_anthropic(oai, model_name):
    choice = (oai.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    blocks = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    for tcall in (msg.get("tool_calls") or []):
        fn = tcall.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {"__raw__": fn.get("arguments")}
        blocks.append({"type": "tool_use", "id": tcall.get("id", ""),
                       "name": fn.get("name", ""), "input": args})
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    usage = oai.get("usage", {}) or {}
    return {
        "id": oai.get("id", "msg_local"),
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": blocks,
        "stop_reason": STOP_MAP.get(choice.get("finish_reason"), "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ---------- HTTP plumbing ----------

def upstream_request(payload, stream):
    conn = http.client.HTTPConnection(UP_HOST, UP_PORT, timeout=900)
    conn.request("POST", "/v1/chat/completions",
                 body=json.dumps(payload).encode(),
                 headers={"Content-Type": "application/json"})
    return conn, conn.getresponse()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # silence default noisy logging
        pass

    def _send_json(self, code, obj, extra=None):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _anthropic_error(self, code, etype, message, streaming=False):
        log("ERROR %d %s: %s" % (code, etype, message))
        if streaming:
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            ev = {"type": "error", "error": {"type": etype, "message": message}}
            self.wfile.write(b"event: error\ndata: " + json.dumps(ev).encode() + b"\n\n")
        else:
            self._send_json(code, {"type": "error",
                                   "error": {"type": etype, "message": message}})

    def do_GET(self):
        if self.path in ("/", "/health", "/v1/health"):
            self._send_json(200, {"status": "ok", "proxy": "anthropic->openai",
                                  "upstream": UPSTREAM})
        else:
            self._send_json(404, {"type": "error",
                                  "error": {"type": "not_found", "message": self.path}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except Exception as e:
            return self._anthropic_error(400, "invalid_request_error", "bad JSON: %s" % e)

        path = self.path.split("?")[0]

        if path == "/v1/messages/count_tokens":
            approx = est_tokens(json.dumps(body.get("system", ""))) + \
                     sum(est_tokens(json.dumps(m)) for m in body.get("messages", [])) + \
                     sum(est_tokens(json.dumps(t)) for t in body.get("tools", []))
            return self._send_json(200, {"input_tokens": approx})

        if path != "/v1/messages":
            return self._anthropic_error(404, "not_found_error", self.path)

        stream = bool(body.get("stream"))
        # ---- log what Claude Code actually sent ----
        sys_txt = _text_from_blocks(body.get("system", "") or "")
        tools = body.get("tools", []) or []
        msg_chars = sum(len(json.dumps(m)) for m in body.get("messages", []))
        tool_chars = sum(len(json.dumps(t)) for t in tools)
        est_in = est_tokens(sys_txt) + msg_chars // 4 + tool_chars // 4
        log("REQ stream=%s msgs=%d tools=%d sys~%dtok tools~%dtok total_in~%dtok max_tokens=%s temp=%s"
            % (stream, len(body.get("messages", [])), len(tools),
               est_tokens(sys_txt), tool_chars // 4, est_in,
               body.get("max_tokens"), body.get("temperature")))
        if tools:
            log("     tool names: " + ", ".join(t.get("name", "?") for t in tools))

        try:
            payload = anthropic_to_openai(body)
        except Exception as e:
            return self._anthropic_error(500, "api_error", "translate failed: %s" % e)

        t0 = time.time()
        try:
            conn, resp = upstream_request(payload, stream)
        except Exception as e:
            return self._anthropic_error(502, "api_error",
                                        "upstream connect failed: %s" % e, streaming=stream)

        if resp.status != 200:
            err_body = resp.read().decode("utf-8", "replace")[:800]
            conn.close()
            return self._anthropic_error(502, "api_error",
                                         "upstream %d: %s" % (resp.status, err_body),
                                         streaming=stream)

        if not stream:
            data = resp.read()
            conn.close()
            try:
                oai = json.loads(data)
            except Exception as e:
                return self._anthropic_error(502, "api_error", "bad upstream json: %s" % e)
            out = openai_to_anthropic(oai, body.get("model", "local"))
            dt = time.time() - t0
            log("RESP non-stream %.1fs in=%d out=%d stop=%s"
                % (dt, out["usage"]["input_tokens"], out["usage"]["output_tokens"],
                   out["stop_reason"]))
            return self._send_json(200, out)

        # ---- streaming: OpenAI SSE -> Anthropic SSE ----
        self.close_connection = True   # SSE + HTTP/1.1: signal EOF by closing
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def sse(event, obj):
            self.wfile.write(("event: %s\ndata: %s\n\n" % (event, json.dumps(obj))).encode())
            self.wfile.flush()

        msg_id = "msg_local_%d" % int(t0)
        sse("message_start", {"type": "message_start", "message": {
            "id": msg_id, "type": "message", "role": "assistant",
            "model": body.get("model", "local"), "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": est_in, "output_tokens": 0}}})
        sse("ping", {"type": "ping"})

        block_idx = -1
        text_open = False
        tool_open = False
        cur_tool_key = None
        first_tok_t = None
        out_tok = 0
        finish = None

        def close_block():
            nonlocal text_open, tool_open
            if text_open or tool_open:
                sse("content_block_stop", {"type": "content_block_stop", "index": block_idx})
            text_open = tool_open = False

        try:
            while True:
                line = resp.readline()
                if not line:
                    break
                line = line.strip()
                if not line or not line.startswith(b"data:"):
                    continue
                payload_s = line[5:].strip()
                if payload_s == b"[DONE]":
                    break
                try:
                    ev = json.loads(payload_s)
                except Exception:
                    continue
                ch = (ev.get("choices") or [{}])[0]
                delta = ch.get("delta", {}) or {}
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]

                # text delta
                dtext = delta.get("content")
                if dtext:
                    if first_tok_t is None:
                        first_tok_t = time.time()
                    if tool_open:
                        close_block()
                    if not text_open:
                        block_idx += 1
                        text_open = True
                        sse("content_block_start", {"type": "content_block_start",
                            "index": block_idx,
                            "content_block": {"type": "text", "text": ""}})
                    sse("content_block_delta", {"type": "content_block_delta",
                        "index": block_idx,
                        "delta": {"type": "text_delta", "text": dtext}})
                    out_tok += 1

                # tool call deltas
                for tc in (delta.get("tool_calls") or []):
                    if first_tok_t is None:
                        first_tok_t = time.time()
                    fn = tc.get("function", {}) or {}
                    key = tc.get("id") or ("idx-%s" % tc.get("index", 0))
                    is_new = (fn.get("name") or tc.get("id")) and key != cur_tool_key
                    if is_new:
                        if text_open or tool_open:
                            close_block()
                        block_idx += 1
                        tool_open = True
                        cur_tool_key = key
                        sse("content_block_start", {"type": "content_block_start",
                            "index": block_idx,
                            "content_block": {"type": "tool_use",
                                              "id": tc.get("id") or ("toolu_local_%d" % block_idx),
                                              "name": fn.get("name", ""), "input": {}}})
                    frag = fn.get("arguments")
                    if frag and tool_open:
                        sse("content_block_delta", {"type": "content_block_delta",
                            "index": block_idx,
                            "delta": {"type": "input_json_delta", "partial_json": frag}})
        finally:
            conn.close()

        close_block()
        sse("message_delta", {"type": "message_delta",
            "delta": {"stop_reason": STOP_MAP.get(finish, "end_turn"),
                      "stop_sequence": None},
            "usage": {"output_tokens": out_tok}})
        sse("message_stop", {"type": "message_stop"})
        dt = time.time() - t0
        ttft = (first_tok_t - t0) if first_tok_t else -1
        tps = out_tok / (dt - ttft) if (first_tok_t and dt > ttft) else 0
        log("RESP stream %.1fs ttft=%.2fs out~%dtok %.1ftok/s stop=%s"
            % (dt, ttft, out_tok, tps, STOP_MAP.get(finish, "end_turn")))


if __name__ == "__main__":
    log("proxy up on http://%s:%d  ->  %s  (model=%s)  log<=%dB x%d" %
        (PROXY_HOST, PROXY_PORT, UPSTREAM, PROXY_MODEL,
         PROXY_LOG_MAX_BYTES, PROXY_LOG_BACKUPS + 1))
    srv = ThreadingHTTPServer((PROXY_HOST, PROXY_PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("proxy shutting down")
