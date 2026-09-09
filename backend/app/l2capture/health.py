"""
L2 SnapQuote capture -- DAILY DATA-QUALITY / HEALTH CHECK.

Read-only over data/l2_capture.db. For one session date, per captured symbol, it
computes: rows, packet rate, expected-vs-actual, timestamp gaps, stale packets,
L1 / L2 completeness, clean-book %, volume + OI continuity, null fields, session
coverage, and a 0-100 Data-Quality Score (multiplicative penalties, per
ORDERFLOW_ENGINE_V2_SPEC.md Part 4). Writes an auditable .md + .json and appends
one line to a running TSV log.

    python -m app.l2capture.health [--date YYYY-MM-DD] [--db PATH] [--out DIR]

This is capture QA only -- it tunes nothing, feeds no model, touches no order
path. The per-symbol "baseline_hz" values are capture-health sanity anchors
(observed, adjustable), NOT strategy parameters.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_DB = os.path.join(_ROOT, "data", "l2_capture.db")
_DEFAULT_OUT = os.path.join(_ROOT, "data", "research", "orderflow", "health")

# per-symbol nominal trading window (IST minutes) + a capture-rate sanity anchor
_WINDOWS = {
    "NIFTY":      {"start": 9 * 60 + 15, "end": 15 * 60 + 30, "baseline_hz": 1.4},
    "BANKNIFTY":  {"start": 9 * 60 + 15, "end": 15 * 60 + 30, "baseline_hz": 1.4},
    "CRUDEOIL":   {"start": 9 * 60,      "end": 23 * 60 + 30, "baseline_hz": 0.9},
    "NATURALGAS": {"start": 9 * 60,      "end": 23 * 60 + 30, "baseline_hz": 0.5},
}
_CRIT_FIELDS = ("ltp", "ltq", "volume", "oi", "bid", "ask", "bid_qty", "ask_qty", "depth_json")
_STALE_LAG_SEC = 150.0


def _iso_epoch(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def _parse_depth(dj):
    try:
        d = json.loads(dj) if isinstance(dj, str) else dj
        return d.get("buy") or [], d.get("sell") or []
    except Exception:
        return [], []


def _symbol_report(rows: list[sqlite3.Row], sym: str) -> dict:
    n = len(rows)
    w = _WINDOWS.get(sym, {"start": 0, "end": 24 * 60, "baseline_hz": 0.5})
    if n == 0:
        return {"symbol": sym, "rows": 0, "verdict": "FAIL", "dqs": 0.0,
                "reason": "no rows captured for this session"}

    ex = [r["exch_ts_ms"] for r in rows if r["exch_ts_ms"]]
    ex.sort()
    span_s = (ex[-1] - ex[0]) / 1000.0 if len(ex) > 1 else 0.0
    first_ist = rows[0]["exch_ts"] or rows[0]["received_ts"]
    last_ist = rows[-1]["exch_ts"] or rows[-1]["received_ts"]

    # inter-arrival gaps
    dts = [(ex[i] - ex[i - 1]) / 1000.0 for i in range(1, len(ex)) if ex[i] >= ex[i - 1]]
    dts_pos = [d for d in dts if d > 0]
    med_dt = statistics.median(dts_pos) if dts_pos else None
    p90_dt = (sorted(dts_pos)[int(len(dts_pos) * 0.9)] if dts_pos else None)
    max_dt = max(dts) if dts else None
    gap5 = sum(1 for d in dts if d > 5)
    gap30 = sum(1 for d in dts if d > 30)
    gap60 = sum(1 for d in dts if d > 60)
    gap_time_frac = (sum(d for d in dts if d > 5) / span_s) if span_s > 0 else 0.0
    rate_hz = (n / span_s) if span_s > 0 else 0.0

    # expected vs actual over the captured span
    exp_rows = span_s * w["baseline_hz"]
    coverage_ratio = (n / exp_rows) if exp_rows > 0 else 0.0

    # session coverage over the NOMINAL window (1-min bins that saw >=1 packet)
    win_min = max(1, w["end"] - w["start"])
    seen = set()
    for r in rows:
        e = _iso_epoch(r["exch_ts"]) or _iso_epoch(r["received_ts"])
        if e is None:
            continue
        ist_min = int(((e + 5.5 * 3600) % 86400) // 60)
        if w["start"] <= ist_min <= w["end"]:
            seen.add(ist_min)
    session_coverage_pct = round(len(seen) / win_min, 4)

    # stale-written: row persisted long after its exchange stamp (post-fix -> 0)
    stale_written = 0
    for r in rows:
        rx, exms = _iso_epoch(r["received_ts"]), r["exch_ts_ms"]
        if rx and exms and (rx - exms / 1000.0) > _STALE_LAG_SEC:
            stale_written += 1

    # dup snap_key
    keys = [r["snap_key"] for r in rows]
    dup_snap_key = len(keys) - len(set(keys))

    # L1 / L2 / clean book
    l1_ok = crossed = l2_ok = clean = 0
    for r in rows:
        b, a = r["bid"], r["ask"]
        if isinstance(b, (int, float)) and isinstance(a, (int, float)) and b > 0 and a > 0:
            l1_ok += 1
            if b >= a:
                crossed += 1
        buy, sell = _parse_depth(r["depth_json"])
        if len(buy) >= 5 and len(sell) >= 5 and all((x.get("quantity") or 0) > 0 for x in buy[:5] + sell[:5]):
            l2_ok += 1
        if buy and sell:
            bp = [x.get("price") or 0 for x in buy]
            sp = [x.get("price") or 0 for x in sell]
            if (bp[0] < sp[0] and all(bp[i] >= bp[i + 1] for i in range(len(bp) - 1))
                    and all(sp[i] <= sp[i + 1] for i in range(len(sp) - 1))):
                clean += 1
    l1_pct = round(l1_ok / n, 4)
    l2_pct = round(l2_ok / n, 4)
    crossed_pct = round(crossed / n, 4)
    clean_book_pct = round(clean / n, 4)

    # volume continuity (order by exch_ts_ms, seq)
    srt = sorted(rows, key=lambda r: (r["exch_ts_ms"] or 0, r["seq"] or 0))
    vols = [r["volume"] for r in srt if isinstance(r["volume"], (int, float))]
    dv = [vols[i] - vols[i - 1] for i in range(1, len(vols))]
    vol_mono_pct = round(sum(1 for d in dv if d >= 0) / len(dv), 4) if dv else 1.0
    vol_breaks = sum(1 for d in dv if d < 0)
    max_vol_drop = min(dv) if dv else 0.0

    # OI continuity (OI steps legitimately; flag only big/negative jumps)
    ois = [r["oi"] for r in srt if isinstance(r["oi"], (int, float)) and r["oi"] > 0]
    doi = [ois[i] - ois[i - 1] for i in range(1, len(ois))]
    oi_big_jump = sum(1 for i, d in enumerate(doi) if ois[i] and abs(d) > 0.05 * ois[i])
    oi_null_pct = round(sum(1 for r in rows if r["oi"] in (None, 0)) / n, 4)

    # null / invalid critical fields
    nulls = {}
    for f in _CRIT_FIELDS:
        bad = sum(1 for r in rows if r[f] is None or (f != "depth_json" and r[f] == 0))
        nulls[f] = round(bad / n, 4)
    worst_null = max(nulls.values())

    # ---- Data-Quality Score : 100 * prod(1 - penalty) ----
    p = {
        "coverage":  _clamp(1 - session_coverage_pct) * 0.60,
        "gaps":      _clamp(gap_time_frac) * 0.40,
        "l1":        _clamp(1 - l1_pct) * 0.35,
        "l2":        _clamp(1 - l2_pct) * 0.30,
        "clean_book": _clamp(1 - clean_book_pct) * 0.20,
        "volume":    _clamp(1 - vol_mono_pct) * 0.35,
        "stale":     _clamp(stale_written / n * 5) * 0.50,
        "dups":      _clamp(dup_snap_key / n * 5) * 0.30,
        "nulls":     _clamp(worst_null) * 0.30,
        "rate":      _clamp(1 - min(1.0, rate_hz / max(1e-9, w["baseline_hz"]))) * 0.40,
    }
    dqs = 100.0
    for v in p.values():
        dqs *= (1 - v)
    dqs = round(dqs, 1)

    verdict = ("PASS" if (dqs >= 80 and session_coverage_pct >= 0.90 and l1_pct >= 0.95
                          and vol_mono_pct >= 0.99 and stale_written == 0)
               else "WARN" if dqs >= 60 else "FAIL")

    return {
        "symbol": sym, "rows": n,
        "first_ist": first_ist, "last_ist": last_ist,
        "span_min": round(span_s / 60.0, 1),
        "rate_hz": round(rate_hz, 3),
        "baseline_hz": w["baseline_hz"],
        "expected_rows": int(exp_rows), "coverage_ratio": round(coverage_ratio, 3),
        "session_coverage_pct": session_coverage_pct,
        "gap_median_s": round(med_dt, 2) if med_dt else None,
        "gap_p90_s": round(p90_dt, 2) if p90_dt else None,
        "gap_max_s": round(max_dt, 1) if max_dt else None,
        "gaps_gt_5s": gap5, "gaps_gt_30s": gap30, "gaps_gt_60s": gap60,
        "gap_time_frac": round(gap_time_frac, 4),
        "stale_written": stale_written, "dup_snap_key": dup_snap_key,
        "l1_complete_pct": l1_pct, "l2_complete_pct": l2_pct,
        "crossed_book_pct": crossed_pct, "clean_book_pct": clean_book_pct,
        "volume_monotonic_pct": vol_mono_pct, "volume_breaks": vol_breaks,
        "max_volume_drop": round(max_vol_drop, 1),
        "oi_big_jumps": oi_big_jump, "oi_null_pct": oi_null_pct,
        "null_pct": nulls,
        "dqs": dqs, "dqs_penalties": {k: round(v, 4) for k, v in p.items()},
        "verdict": verdict,
    }


def analyze(db_path: str = _DEFAULT_DB, session_date: str | None = None,
            symbols: list[str] | None = None) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    if session_date is None:
        row = con.execute("SELECT MAX(session_date_ist) FROM snapquote_ticks").fetchone()
        session_date = row[0] if row else None
    rep = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db": db_path, "session_date": session_date, "by_symbol": {},
    }
    if not session_date:
        con.close()
        rep["status"] = "NO_DATA"
        return rep

    syms = symbols or [r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM snapquote_ticks WHERE session_date_ist=? AND symbol IS NOT NULL "
        "ORDER BY symbol", (session_date,))]
    for sym in syms:
        rows = con.execute(
            "SELECT * FROM snapquote_ticks WHERE session_date_ist=? AND symbol=? "
            "ORDER BY exch_ts_ms, seq", (session_date, sym)).fetchall()
        rep["by_symbol"][sym] = _symbol_report(rows, sym)

    # worker-side reject stats for the day (from capture_runs.note JSON)
    rej = {"rejected": 0, "reasons": {}}
    for (note,) in con.execute(
            "SELECT note FROM capture_runs WHERE date(started_ts)=? OR date(ended_ts)=?",
            (session_date, session_date)):
        try:
            d = json.loads(note or "{}")
            rej["rejected"] = max(rej["rejected"], int(d.get("rejected", 0)))
            for k, v in (d.get("reject_reasons") or {}).items():
                rej["reasons"][k] = max(rej["reasons"].get(k, 0), int(v))
        except Exception:
            pass
    rep["worker_rejects"] = rej
    con.close()

    verdicts = [s.get("verdict") for s in rep["by_symbol"].values()]
    rep["status"] = "OK"
    rep["overall"] = ("FAIL" if "FAIL" in verdicts else "WARN" if "WARN" in verdicts
                      else "PASS" if verdicts else "NO_DATA")
    prio = [s["symbol"] for s in rep["by_symbol"].values()]
    rep["p0_pass"] = ("NIFTY" in rep["by_symbol"]
                      and rep["by_symbol"]["NIFTY"].get("verdict") == "PASS"
                      and rep["by_symbol"]["NIFTY"].get("rows", 0) >= 8000)
    return rep


def render_md(rep: dict) -> str:
    L = [f"# L2 SnapQuote capture health -- {rep.get('session_date','?')}", "",
         f"_generated {rep['generated_at']} · db `{os.path.basename(rep['db'])}`_", ""]
    if rep.get("status") != "OK":
        L += [f"**STATUS: {rep.get('status')}** -- nothing to report."]
        return "\n".join(L)
    L += [f"## OVERALL: **{rep['overall']}**    ·    P0 (NIFTY >=8k, PASS): "
          f"**{'YES' if rep['p0_pass'] else 'NO'}**", ""]
    wr = rep.get("worker_rejects", {})
    if wr.get("rejected"):
        L += [f"worker rejected {wr['rejected']} packet(s) today: {wr['reasons']}", ""]
    L += ["| symbol | rows | span min | rate Hz (base) | sess cov % | gap>5/30/60s | max gap s | "
          "L1 % | L2 % | clean book % | vol mono % | vol breaks | stale | dups | worst null % | DQS | verdict |",
          "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|:--:|"]
    for s in rep["by_symbol"].values():
        if s["rows"] == 0:
            L.append(f"| {s['symbol']} | 0 | | | | | | | | | | | | | | 0 | **FAIL** |")
            continue
        wn = max(s["null_pct"].values()) if s.get("null_pct") else 0.0
        L.append(
            f"| {s['symbol']} | {s['rows']} | {s['span_min']} | {s['rate_hz']} ({s['baseline_hz']}) | "
            f"{round(s['session_coverage_pct']*100,1)} | {s['gaps_gt_5s']}/{s['gaps_gt_30s']}/{s['gaps_gt_60s']} | "
            f"{s['gap_max_s']} | {round(s['l1_complete_pct']*100,1)} | {round(s['l2_complete_pct']*100,1)} | "
            f"{round(s['clean_book_pct']*100,1)} | {round(s['volume_monotonic_pct']*100,2)} | {s['volume_breaks']} | "
            f"{s['stale_written']} | {s['dup_snap_key']} | {round(wn*100,2)} | {s['dqs']} | **{s['verdict']}** |")
    L += ["", "### per-symbol detail", ""]
    for s in rep["by_symbol"].values():
        if s["rows"] == 0:
            continue
        L += [f"**{s['symbol']}** {s['first_ist']} .. {s['last_ist']}",
              f"- expected ~{s['expected_rows']} rows over the captured span (coverage ratio {s['coverage_ratio']}); "
              f"actual {s['rows']}",
              f"- gaps: median {s['gap_median_s']}s / p90 {s['gap_p90_s']}s / max {s['gap_max_s']}s; "
              f"{round(s['gap_time_frac']*100,2)}% of span in >5s gaps",
              f"- OI: {s['oi_big_jumps']} big jumps, {round(s['oi_null_pct']*100,2)}% null",
              f"- null %: {s['null_pct']}",
              f"- DQS penalties: {s['dqs_penalties']}", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="L2 SnapQuote capture health check")
    ap.add_argument("--date", default=None, help="session date YYYY-MM-DD (default: latest with data)")
    ap.add_argument("--db", default=_DEFAULT_DB)
    ap.add_argument("--out", default=_DEFAULT_OUT)
    a = ap.parse_args(argv)
    rep = analyze(a.db, a.date)
    os.makedirs(a.out, exist_ok=True)
    d = rep.get("session_date") or datetime.now().strftime("%Y-%m-%d")
    with open(os.path.join(a.out, f"l2_health_{d}.json"), "w") as fh:
        json.dump(rep, fh, indent=2, default=str)
    with open(os.path.join(a.out, f"l2_health_{d}.md"), "w") as fh:
        fh.write(render_md(rep))
    # append a running TSV log line for trend auditing
    logp = os.path.join(a.out, "l2_health_log.tsv")
    new = not os.path.exists(logp)
    with open(logp, "a") as fh:
        if new:
            fh.write("date\toverall\tp0_pass\tsymbol\trows\trate_hz\tsess_cov_pct\tdqs\tverdict\n")
        for s in rep.get("by_symbol", {}).values():
            fh.write(f"{d}\t{rep.get('overall')}\t{rep.get('p0_pass')}\t{s['symbol']}\t{s.get('rows',0)}\t"
                     f"{s.get('rate_hz',0)}\t{round(s.get('session_coverage_pct',0)*100,1)}\t"
                     f"{s.get('dqs',0)}\t{s.get('verdict')}\n")
    print(f"L2 HEALTH {d}: OVERALL={rep.get('overall')} P0_PASS={rep.get('p0_pass')}")
    for s in rep.get("by_symbol", {}).values():
        print(f"  {s['symbol']:12s} rows={s.get('rows',0):6d} rate={s.get('rate_hz',0):>5} Hz "
              f"cov={round(s.get('session_coverage_pct',0)*100,1)}% DQS={s.get('dqs',0):>5} {s.get('verdict')}")
    print(f"  -> {os.path.join(a.out, f'l2_health_{d}.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
