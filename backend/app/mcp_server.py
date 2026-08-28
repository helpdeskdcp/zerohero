"""
Angel One MCP server — the "tool harness" layer.

    Angel One (REST + TOTP)  ->  this MCP server  ->  any MCP client (Claude, etc.)

Exposes Chanakya's live-data + analysis functions as MCP tools over stdio
(JSON-RPC 2.0, newline-delimited). No extra dependency — a minimal hand-rolled
MCP loop. Paper/analysis only: NO order-placement tool exists here.

Run:   python -m app.mcp_server         (from backend/, with .env loaded)

Claude Code / Desktop config:
    {
      "mcpServers": {
        "angelone": {
          "command": "/opt/chanakya-app/backend/venv/bin/python",
          "args": ["-m", "app.mcp_server"],
          "cwd": "/opt/chanakya-app/backend",
          "env": {"CHANAKYA_ENV_FILE": "/opt/chanakya-app/backend/.env"}
        }
      }
    }
"""
import os
import sys
import json

# load .env if python-dotenv is around and a path is hinted / present
try:
    from dotenv import load_dotenv
    load_dotenv(os.environ.get("CHANAKYA_ENV_FILE", ".env"))
except Exception:
    pass

from .connectors import angelone
from .engines.signal_engine import run_signal_engine
from .engines.scalp_engine import run_scalp_engine
from .reversal import detect_reversal

PROTOCOL = "2024-11-05"
SERVER = {"name": "angelone-chanakya", "version": "1.0.0"}

_S = {"type": "string"}
TOOLS = [
    {"name": "angel_candles",
     "description": "Historical OHLCV candles from Angel One for a registry symbol (NIFTY, BANKNIFTY, NATGASMINI, ...). Auto-resolves token, interval and date window.",
     "inputSchema": {"type": "object", "properties": {"symbol": _S, "timeframe": _S}, "required": ["symbol"]}},
    {"name": "angel_ltp",
     "description": "Latest traded price for a registry symbol (last 1m candle close).",
     "inputSchema": {"type": "object", "properties": {"symbol": _S}, "required": ["symbol"]}},
    {"name": "angel_positions",
     "description": "Live net + round-turned positions on the Angel One account, with realised/unrealised P&L.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "angel_signal",
     "description": "Deterministic rule-based signal (direction, regime, entry/target/stop, RR, probability) for a symbol/timeframe.",
     "inputSchema": {"type": "object", "properties": {"symbol": _S, "timeframe": _S}, "required": ["symbol"]}},
    {"name": "angel_scalp",
     "description": "Fast-timeframe scalp read (VWAP reclaim / EMA pullback / momentum break) with tick target/stop.",
     "inputSchema": {"type": "object", "properties": {"symbol": _S, "timeframe": _S}, "required": ["symbol"]}},
    {"name": "angel_reversal",
     "description": "Support/resistance reversal read: is price tagging a swing level and rejecting? Returns CE/PE pick + entry/stop/target if a turn is firing.",
     "inputSchema": {"type": "object", "properties": {"symbol": _S, "timeframe": _S}, "required": ["symbol"]}},
]


def _candles(symbol, timeframe):
    return angelone.fetch_candles(market=None, symbol=symbol, exchange=None, symboltoken=None,
                                  interval=None, fromdate=None, todate=None,
                                  timeframe=timeframe or "15m", instrument="FUT")


def _call(name, args):
    args = args or {}
    sym = args.get("symbol")
    tf = args.get("timeframe") or "15m"
    if name == "angel_positions":
        return angelone.fetch_positions()
    if name == "angel_ltp":
        c = _candles(sym, "1m")
        cds = c.get("candles") or []
        return {"symbol": sym, "data_status": c.get("data_status"),
                "ltp": cds[-1]["c"] if cds else None,
                "as_of": cds[-1]["t"] if cds else None}
    c = _candles(sym, tf)
    if c.get("data_status") != "OK":
        return {"symbol": sym, "data_status": c.get("data_status"), "reason": c.get("reason")}
    if name == "angel_candles":
        return {"symbol": sym, "timeframe": tf, "count": len(c["candles"]),
                "candles": c["candles"][-120:], "resolved": c.get("symboltoken")}
    if name == "angel_signal":
        return run_signal_engine({"symbol": sym, "timeframe": tf, "source": "ANGELONE",
                                  "data_status": "OK", "candles": c["candles"], "config": {}})
    if name == "angel_scalp":
        return run_scalp_engine({"symbol": sym, "timeframe": tf, "candles": c["candles"],
                                 "config": {"ignore_session": True}})
    if name == "angel_reversal":
        r = detect_reversal(c["candles"])
        r["symbol"] = sym
        r["timeframe"] = tf
        return r
    raise ValueError(f"unknown tool {name}")


def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        mid = req.get("id")
        method = req.get("method")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER}})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            p = req.get("params") or {}
            try:
                result = _call(p.get("name"), p.get("arguments"))
                _send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": json.dumps(result, default=str)}]}})
            except Exception as e:
                _send({"jsonrpc": "2.0", "id": mid, "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}]}})
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32601, "message": f"method not found: {method}"}})


if __name__ == "__main__":
    main()
