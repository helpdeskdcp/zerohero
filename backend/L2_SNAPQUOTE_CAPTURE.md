# L2 SnapQuote capture — Angel WS mode 3 (route (a))

Implements `ORDERFLOW_STAGE9_L2_RESEARCH.md` §6b **route (a)**: subscribe the
Angel One WebSocket in **mode 3 (SnapQuote)** and append every packet to a
separate research DB.

**This is market-data capture only.** It emits no BUY/SELL/order/signal, does
not touch `market_history.db` / histcap / the scalp runner's mode-1 feed / any
frozen H1-H7 / trading / execution / risk path. `live_trading` stays `false`.

**OFF by default.** Nothing runs until `L2_CAPTURE_ENABLED=1`.

## What it captures

For the **front-month FUTURE** of `NIFTY`, `CRUDEOIL`, `NATURALGAS` (registry-
resolved), every SnapQuote packet: LTP, LTQ, ATP, cumulative volume,
`tot_buy_qty` / `tot_sell_qty`, day OHLC, OI, LTT, circuit limits, 52-wk H/L, and
the **5-level bid/ask book** (`depth_json`, same shape as
`market_history.quote_snapshots.depth_json`).

Native cadence is ~1 Hz per instrument (much finer than histcap's ~25–30 s REST
poll). It is still **not** an aggressor tape — Angel mode 3 carries no
per-trade side. So this unlocks tiers **T1 (L2 depth) + T2 (trade size)** from
§6b; **T3 (aggressor) / T4 (event stream)** stay `UNOBSERVABLE`.

## Files

| file | role |
|---|---|
| `app/l2capture/snapquote.py` | pure 379-byte mode-3 binary parser (`parse_snapquote`), no I/O |
| `app/l2capture/store.py` | `L2Store` — append-only SQLite sink, `INSERT OR IGNORE`, raw frames gzip+sha256 |
| `app/l2capture/worker.py` | `L2CaptureWorker` — own WS connection, leader lease, market-hours gate, reconnect |
| `app/l2capture/__main__.py` | `python -m app.l2capture` standalone runner |
| `tests/test_l2capture_snapquote.py` | parser + store unit tests (offline) |

## Storage

Separate DB **`backend/data/l2_capture.db`** (git-ignored, WAL). Nothing is ever
UPDATEd or DELETEd on the tick table; a gap is left as a gap, never filled.

- `snapquote_ticks` — one row per packet. `UNIQUE(snap_key)` where
  `snap_key = "<token>:<seq>:<exch_ts_ms>"`.
- `raw_frames` — every binary frame, `gzip_b64` + `UNIQUE(sha256)`.
- `capture_runs` — one row per connected session (started/ended, tokens, counts).

## Enable

**In the existing service** — add to `backend/.env` and restart:

```
L2_CAPTURE_ENABLED=1
# optional overrides:
# L2_CAPTURE_SYMBOLS=NIFTY,CRUDEOIL,NATURALGAS
# L2_CAPTURE_DB=/root/zerohero/backend/data/l2_capture.db
# L2_CAPTURE_STORE_RAW=1
# L2_CAPTURE_FLUSH_SEC=2.0
```
```
systemctl restart oi-dashboard.service
```
It starts alongside histcap in the startup hook (guarded; a failure only logs).

**Standalone** (own process, e.g. tmux or a dedicated unit):

```
cd /root/zerohero/backend && venv/bin/python -m app.l2capture
```

Example systemd unit (`/etc/systemd/system/l2capture.service`):

```
[Unit]
Description=Angel WS mode-3 SnapQuote capture
After=network-online.target

[Service]
WorkingDirectory=/root/zerohero/backend
Environment=L2_CAPTURE_ENABLED=1
ExecStart=/root/zerohero/backend/venv/bin/python -m app.l2capture
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

(Run it **either** in the service via the env flag **or** as this unit — not
both; the leader lease makes a double-run harmless but pointless.)

## Verify capture

```python
from app.l2capture.store import L2Store
import json; print(json.dumps(L2Store().summary(), indent=2, default=str))
```

## When enough has accumulated

Per §6b: **≥ 40 sessions across ≥ 2 volatility regimes** before
`orderflow_l2_imbalance_probe.py` / Stage-9 Phases B–E are run for real. At
~1 Hz that is roughly a 2-month forward capture. The passive-book imbalance
calculation in `ORDERFLOW_L2_IMBALANCE_GATE.md` reads `depth_json` unchanged —
point it at `l2_capture.db` once the coverage gate is met.
