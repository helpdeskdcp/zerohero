# AngelOne index-feed freeze — investigation (triggered 2026-09-10)

_Read-only forensic pass over captured data. Nothing in zerohero was changed by
this investigation. Companion tool: `scripts/stale_feed_watchdog.py`._

## Trigger

Operator report: on 2026-09-10 a profitable SENSEX position could not be exited
("AngelOne hung") ~15:15–15:40 IST, ending in a loss. Asked for a check of the
captured API data for that window.

## First pass (WRONG conclusion — corrected below)

`market_history.db` `quote_snapshots` for 2026-09-10 09:45–10:10 UTC
(15:15–15:40 IST) showed **all index LTPs frozen** at a single value with
`quote_status='OK'`, then a single jump right at the close:

| index | frozen value | froze (IST) | unfroze (IST) |
|---|---|---|---|
| SENSEX | 74,629.50 | 15:15:26 | 15:29:05 |
| NIFTY | 23,389.25 | 15:15:26 | 15:29:05 |
| BANKEX | 63,641.80 | 15:15:26 | 15:29:05 |
| BANKNIFTY | 56,262.50 | 15:15:26 | 15:29:05 |
| FINNIFTY | 25,411.25 | 15:15:26 | 15:29:05 |
| MIDCPNIFTY | 14,485.75 | 15:15:26 | 15:28:24 |

Option premiums on the same underlyings kept moving (SENSEX 74300 CE ran
~300 → ~750 during the freeze). This was initially read as a market-wide
AngelOne outage matching the operator's timeframe.

## Corrected finding

Running the watchdog across prior sessions shows the **same freeze every trading
day** — it is a standing AngelOne behaviour, not a 10-Sep incident:

| session | SENSEX index REST feed frozen (IST) | dur |
|---|---|---|
| 2026-09-04 | 15:14:32 → 15:27:06 | 754 s |
| 2026-09-07 | 15:27:33 → 15:39:28 | 716 s |
| 2026-09-08 | 15:15:17 → 15:27:38 | 741 s |
| 2026-09-09 | 15:15:28 → 15:27:47 | 739 s |
| 2026-09-10 | 15:15:26 → 15:29:05 | 820 s |

And the **AngelOne WebSocket SnapQuote feed** (a different endpoint, captured in
`l2_capture.db` for NIFTY/CRUDE/GAS futures) was **ticking normally through
15:15–15:30 on 09-09 and 09-10** — 250–380 ticks per 2 min, price moving live
(10-Sep NIFTY-FUT rallied 23,465 → 23,515 in that window).

### What is actually true

- AngelOne's **REST `quoteData` / `getMarketData` INDEX quotes stop updating
  ~15:14–15:20 IST every day** and only refresh to the official close
  ~15:27–15:40. All indices at once. Options in the same REST response keep
  updating. `quote_status` stays `OK`, so age-based freshness checks don't fire.
- The **WS feed is unaffected** — the real move is available in real time there.
- 2026-09-10 is **indistinguishable** from 07/08/09-Sep in the captured index
  data. Nothing anomalous about that specific day on the feed side.
- zerohero's capture pipeline was healthy throughout (capture_runs every
  ~40–70 s, `auth_ok=1`, ~400 quotes/run, zero capture errors). It faithfully
  recorded AngelOne's stale REST values.
- zerohero placed no SENSEX trade/signal in the window (it is PAPER and does not
  trade SENSEX cash). The operator's loss was on a separate manual terminal; an
  app "hang" is a client-side symptom not observable from captured feed data.

### Implication for a broker complaint

Frame it as the standing issue, not an incident: *"AngelOne REST index quotes
freeze for ~13 min before every close while the WebSocket feed keeps updating."*
A "you had an outage on 10-Sep" claim is not supportable — AngelOne can show it
is their normal REST behaviour.

## The gap this exposed, and the fix

`app/connectors/angelone.py::_freshness_meta` tracks **quote age**, not **value
staleness** — a feed that keeps returning `OK` with a frozen number passes every
existing gate. `scripts/stale_feed_watchdog.py` is the missing value-staleness
check, run post-session (cron: `scripts/stale_feed_watchdog_cron.sh`, 16:15 IST).

- Detects: index LTP identical across ≥ 3 snaps AND ≥ 120 s, `status=OK`, during
  market OPEN hours.
- Corroborates with option-LTP movement on the same underlying in that window.
- Classifies the benign daily pre-close freeze (starts 15:05–15:35, ends ≤ 15:45
  IST) as **`KNOWN_PRECLOSE`** → logged, never paged.
- A freeze **outside** that window with options still moving → **`CONFIRMED`**
  anomaly → exit 2 → Telegram page.
- `--self-test` replays 07–10 Sep and asserts the pre-close freeze is detected
  *and* tagged `KNOWN_PRECLOSE` (no false alarm), plus a synthetic mid-session
  freeze is tagged `CONFIRMED`.

Not wired into any live gate. Monitor only.
