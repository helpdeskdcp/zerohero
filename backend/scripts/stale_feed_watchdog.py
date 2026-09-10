#!/usr/bin/env python3
"""
stale_feed_watchdog.py  --  READ-ONLY.

Detects a *stale-value* feed freeze: the broker keeps answering (quote_status
'OK', capture cycles running) but an index LTP stops moving -- the same value
repeated across many consecutive snapshots while the real market moves.
`_freshness_meta` only tracks *age*, not value-staleness; nothing else flags
"LTP identical for N snaps with status OK". This is that missing check, run as a
post-session monitor (same pattern as trending_up_haircut_monitor.py). It
changes nothing.

KNOWN BENIGN PATTERN -- do not alert on it:
  AngelOne's REST `quoteData` INDEX quotes stop updating ~15:14-15:20 IST and
  only refresh to the official close ~15:27-15:40, *every trading day*, for all
  indices at once. The WS SnapQuote feed (separate endpoint) is unaffected -- it
  keeps ticking the real move. See ANGELONE_FEED_GLITCH_2026-09-10.md for the
  investigation (07/08/09/10-Sep all identical). A frozen run whose window sits
  inside the pre-close approach is tagged KNOWN_PRECLOSE, not CONFIRMED, so cron
  stays quiet on it. A freeze OUTSIDE that window (mid-session, or much longer)
  is a real anomaly and is tagged CONFIRMED / SUSPECT.

  venv/bin/python scripts/stale_feed_watchdog.py               # today, full report
  venv/bin/python scripts/stale_feed_watchdog.py --summary     # one-line (Telegram)
  venv/bin/python scripts/stale_feed_watchdog.py --date 2026-09-10
  venv/bin/python scripts/stale_feed_watchdog.py --self-test   # replays 07-10 Sep

Exit code: 0 normally; 2 if an ANOMALOUS (non-KNOWN_PRECLOSE) CONFIRMED run is
found -- so the cron wrapper can alert only on the real thing. --self-test exits
1 on failure.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DB = _ROOT / "data" / "market_history.db"
_IST = timezone(timedelta(hours=5, minutes=30))

# symbol -> exchange (for market-hours gating). BSE indices mirror NSE hours.
_EXCH = {"SENSEX": "BSE", "BANKEX": "BSE"}          # everything else -> NSE

# defaults -- a frozen run must clear ALL of these to be reported
MIN_SNAPS = 3          # >= this many identical consecutive snapshots
MIN_SECONDS = 120      # spanning at least this long
OPT_MOVES_CONFIRM = 3  # distinct option LTP values in the window => market was moving

# the benign daily AngelOne REST-index pre-close freeze: a run that STARTS in
# this IST minute window and ENDS by PRECLOSE_END_MAX is tagged KNOWN_PRECLOSE.
PRECLOSE_START_LO = 15 * 60 + 5     # 15:05 IST
PRECLOSE_START_HI = 15 * 60 + 35    # 15:35 IST
PRECLOSE_END_MAX = 15 * 60 + 45     # 15:45 IST


def _mod_ist(ts: str) -> int:
    d = _parse(ts).astimezone(_IST)
    return d.hour * 60 + d.minute


def _is_known_preclose(start_ts: str, end_ts: str) -> bool:
    return (PRECLOSE_START_LO <= _mod_ist(start_ts) <= PRECLOSE_START_HI
            and _mod_ist(end_ts) <= PRECLOSE_END_MAX)


def _con():
    return sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _ist(ts: str) -> str:
    return _parse(ts).astimezone(_IST).strftime("%H:%M:%S")


def _market_open_at(symbol: str, ts: str) -> bool:
    """OPEN window for the symbol's exchange at snapshot time. Falls back to a
    plain 09:15-15:30 IST check if the app calendar can't be imported."""
    exch = _EXCH.get(symbol, "NSE")
    dt_ist = _parse(ts).astimezone(_IST)
    try:
        sys.path.insert(0, str(_ROOT))
        from app.market_calendar import segment_status  # noqa: E402
        return segment_status(exch, dt_ist) == "OPEN"
    except Exception:
        m = dt_ist.hour * 60 + dt_ist.minute
        return dt_ist.weekday() < 5 and 555 <= m <= 930  # 09:15..15:30


def _index_symbols(con, date: str) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM quote_snapshots "
        "WHERE kind='INDEX' AND session_date_ist=? ORDER BY symbol", (date,))]


def _series(con, symbol: str, date: str):
    return list(con.execute(
        "SELECT received_ts, ltp, quote_status FROM quote_snapshots "
        "WHERE kind='INDEX' AND symbol=? AND session_date_ist=? "
        "AND ltp IS NOT NULL ORDER BY received_ts", (symbol, date)))


def _option_moves(con, symbol: str, date: str, t0: str, t1: str) -> int:
    """distinct option LTP values seen for this underlying within [t0, t1]."""
    row = con.execute(
        "SELECT COUNT(DISTINCT ltp) FROM quote_snapshots "
        "WHERE kind='OPTION' AND symbol=? AND session_date_ist=? "
        "AND received_ts BETWEEN ? AND ? AND ltp IS NOT NULL",
        (symbol, date, t0, t1)).fetchone()
    return int(row[0] or 0)


def find_frozen_runs(con, symbol: str, date: str,
                     min_snaps: int, min_seconds: int) -> list[dict]:
    rows = _series(con, symbol, date)
    if len(rows) < min_snaps:
        return []
    runs = []
    cur = None
    for received_ts, ltp, status in rows:
        if cur and ltp == cur["ltp"]:
            cur["end"] = received_ts
            cur["n"] += 1
            cur["all_ok"] = cur["all_ok"] and (status == "OK")
        else:
            if cur:
                runs.append(cur)
            cur = {"ltp": ltp, "start": received_ts, "end": received_ts,
                   "n": 1, "all_ok": status == "OK"}
    if cur:
        runs.append(cur)

    out = []
    for r in runs:
        dur = (_parse(r["end"]) - _parse(r["start"])).total_seconds()
        if r["n"] < min_snaps or dur < min_seconds or not r["all_ok"]:
            continue
        mid = _parse(r["start"]) + (_parse(r["end"]) - _parse(r["start"])) / 2
        if not _market_open_at(symbol, mid.isoformat().replace("+00:00", "Z")):
            continue
        opt_moves = _option_moves(con, symbol, date, r["start"], r["end"])
        if _is_known_preclose(r["start"], r["end"]):
            verdict = "KNOWN_PRECLOSE"
        elif opt_moves >= OPT_MOVES_CONFIRM:
            verdict = "CONFIRMED"
        else:
            verdict = "SUSPECT"
        out.append({
            "symbol": symbol, "value": r["ltp"], "n": r["n"],
            "start": r["start"], "end": r["end"], "dur_s": round(dur),
            "opt_moves": opt_moves, "verdict": verdict,
            "anomalous": verdict != "KNOWN_PRECLOSE",
        })
    return out


def scan(date: str, min_snaps: int = MIN_SNAPS, min_seconds: int = MIN_SECONDS,
         con=None) -> dict:
    own = con is None
    if own:
        con = _con()
    con.row_factory = sqlite3.Row
    try:
        syms = _index_symbols(con, date)
        all_runs = []
        for s in syms:
            all_runs.extend(find_frozen_runs(con, s, date, min_snaps, min_seconds))
    finally:
        if own:
            con.close()
    all_runs.sort(key=lambda r: (-r["dur_s"], r["symbol"]))
    anomalous = [r for r in all_runs if r["anomalous"]]

    # cross-index simultaneity, computed over the ANOMALOUS runs only (the
    # KNOWN_PRECLOSE freeze is market-wide every day -- not interesting).
    market_wide = False
    if len(anomalous) >= 3:
        starts = sorted(_parse(r["start"]) for r in anomalous)
        near = sum(1 for t in starts if (t - starts[0]).total_seconds() <= 120)
        market_wide = near >= 3

    return {"date": date, "symbols_scanned": syms, "runs": all_runs,
            "anomalous": anomalous, "market_wide": market_wide,
            "min_snaps": min_snaps, "min_seconds": min_seconds}


# --------------------------------------------------------------------------- #
#  presentation                                                              #
# --------------------------------------------------------------------------- #
def summary_line(res: dict) -> str:
    d = res["date"]
    anom = res["anomalous"]
    n_known = len(res["runs"]) - len(anom)
    if not anom:
        tail = f" ({n_known} KNOWN_PRECLOSE, expected)" if n_known else ""
        return f"stale-feed watchdog {d}: CLEAN -- no anomalous index freeze{tail}"
    w = anom[0]
    scope = "market-wide" if res["market_wide"] else "single-symbol"
    head = (f"stale-feed watchdog {d}: *** {len(anom)} ANOMALOUS frozen index run(s) "
            f"[{scope}] *** ({n_known} known pre-close)")
    worst = (f"worst {w['symbol']} {w['value']} "
             f"{_ist(w['start'])}-{_ist(w['end'])} IST "
             f"({w['dur_s']}s, {w['n']} snaps, opt_moves={w['opt_moves']}) {w['verdict']}")
    return head + "\n" + worst


def full_report(res: dict) -> str:
    P = []
    P.append("=" * 92)
    P.append(f"STALE-VALUE FEED WATCHDOG  --  {res['date']}  [READ-ONLY]")
    P.append("=" * 92)
    P.append(f"index symbols scanned: {', '.join(res['symbols_scanned']) or '(none)'}")
    P.append(f"thresholds: >= {res['min_snaps']} identical snaps AND >= {res['min_seconds']}s, "
             f"quote_status OK, during market OPEN hours")
    P.append("")
    if not res["runs"]:
        P.append("RESULT: CLEAN -- no frozen index LTP runs cleared the thresholds.")
        return "\n".join(P)

    anom = res["anomalous"]
    if anom:
        P.append(f"RESULT: *** {len(anom)} ANOMALOUS frozen run(s)"
                 + ("  --  MARKET-WIDE" if res["market_wide"] else "") + " ***")
    else:
        P.append(f"RESULT: CLEAN -- {len(res['runs'])} frozen run(s), all KNOWN_PRECLOSE (expected)")
    P.append("")
    P.append(f"  {'symbol':<11} {'frozen value':>13}  {'window IST':<21} {'dur':>6} {'snaps':>6} "
             f"{'opt_moves':>10}  verdict")
    P.append("  " + "-" * 86)
    for r in res["runs"]:
        P.append(f"  {r['symbol']:<11} {r['value']:>13} "
                 f" {_ist(r['start'])}-{_ist(r['end'])}  {r['dur_s']:>5}s {r['n']:>6} "
                 f"{r['opt_moves']:>10}  {r['verdict']}")
    P.append("")
    P.append("  KNOWN_PRECLOSE = the benign daily AngelOne REST-index freeze (~15:15-15:40 IST,")
    P.append("    all indices, every session; WS feed unaffected). Not an alert.")
    P.append("  opt_moves = distinct option LTP values for that underlying inside the frozen")
    P.append("    window. High opt_moves + frozen index (outside the known window) => CONFIRMED")
    P.append("    (market moved, index feed stuck). Low opt_moves => SUSPECT (lull / halt).")
    P.append("")
    if anom:
        P.append(f"  ==> {len(anom)} ANOMALOUS stale-feed run(s) -- a freeze outside the known")
        P.append("      pre-close pattern. If it overlaps a live position, the broker feed -- not")
        P.append("      the strategy -- is the cause. Keep the timestamps; see")
        P.append("      ANGELONE_FEED_GLITCH_2026-09-10.md for context and the complaint framing.")
    else:
        P.append("  ==> nothing to action. The pre-close freeze is a standing AngelOne REST quirk,")
        P.append("      documented, and does not affect the WS path or (paper) execution.")
    return "\n".join(P)


# --------------------------------------------------------------------------- #
#  self-test                                                                 #
#  1. detection works on real data (07-10 Sep all show the pre-close freeze)  #
#  2. that freeze is tagged KNOWN_PRECLOSE -> NOT an alert                    #
#  3. a synthetic mid-session freeze IS tagged CONFIRMED + anomalous         #
# --------------------------------------------------------------------------- #
def _synthetic_anomaly_check() -> bool:
    """Pure-logic check of the classifier, no DB."""
    # mid-session (12:00-12:10 IST) 3-min freeze, options moving -> CONFIRMED anomaly
    a = _is_known_preclose("2026-09-10T06:30:00Z", "2026-09-10T06:33:00Z")   # 12:00-12:03 IST
    b = _is_known_preclose("2026-09-10T09:46:00Z", "2026-09-10T09:59:00Z")   # 15:16-15:29 IST
    ok = (a is False) and (b is True)
    print(("  PASS " if ok else "  FAIL ")
          + f"classifier: mid-session freeze anomalous ({not a}), pre-close benign ({b})")
    return ok


def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  PASS " if cond else "  FAIL ") + msg)
        ok = ok and cond

    print("SELF-TEST  --  stale-value feed watchdog")
    ok = _synthetic_anomaly_check() and ok

    date = "2026-09-10"
    if not _DB.exists():
        print(f"  SKIP db checks: {_DB} not present")
        print("SELF-TEST:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    con = _con()
    try:
        has = con.execute(
            "SELECT COUNT(*) FROM quote_snapshots WHERE session_date_ist=? AND kind='INDEX'",
            (date,)).fetchone()[0]
    finally:
        con.close()
    if not has:
        print(f"  SKIP db checks: no {date} INDEX rows")
        print("SELF-TEST:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    res = scan(date)
    sensex = [r for r in res["runs"] if r["symbol"] == "SENSEX"]
    check(bool(sensex), f"{date}: SENSEX frozen run detected")
    if sensex:
        s = sensex[0]
        check(700 <= s["dur_s"] <= 1000, f"SENSEX freeze ~820s (got {s['dur_s']}s)")
        check(abs(s["value"] - 74629.50) < 0.01, f"frozen value 74629.50 (got {s['value']})")
        check(s["verdict"] == "KNOWN_PRECLOSE",
              f"pre-close freeze tagged KNOWN_PRECLOSE (got {s['verdict']})")
    check(len(res["runs"]) >= 4, f"all indices caught ({len(res['runs'])} frozen runs)")
    check(not res["anomalous"], f"{date}: 0 anomalous runs (got {len(res['anomalous'])}) -- no false alert")
    check(res["market_wide"] is False, "market_wide flag off (only anomalous runs count)")
    print("SELF-TEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="stale-value feed watchdog (read-only)")
    ap.add_argument("--date", default=datetime.now(_IST).strftime("%Y-%m-%d"),
                    help="session date IST (default: today)")
    ap.add_argument("--min-snaps", type=int, default=MIN_SNAPS)
    ap.add_argument("--min-seconds", type=int, default=MIN_SECONDS)
    ap.add_argument("--summary", action="store_true", help="one-line output for Telegram")
    ap.add_argument("--self-test", action="store_true", help="replay the 2026-09-10 glitch")
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    res = scan(a.date, a.min_snaps, a.min_seconds)
    print(summary_line(res) if a.summary else full_report(res))
    # exit 2 only for a real anomaly, so the cron wrapper alerts on that alone
    return 2 if res["anomalous"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
