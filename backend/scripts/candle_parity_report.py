"""READ-ONLY candle parity diagnostic. Opens market_history.db with mode=ro, never writes the DB, never changes the candle builder.
Reports per 5m bucket: raw 1m rows (UTC bar_start, IST, source, OHLC, volume), independent aggregation (O=first, H=max, L=min, C=last),
stored broker 5m, optional Yahoo CSV and optional TV reference CSV, a field-by-field abs-diff table, and missing-1m intervals classified.
Usage: python scripts/candle_parity_report.py --date 2026-10-12 --buckets 09:20,09:25,09:30 [--yahoo csv] [--tv csv] [--out md]
Verdict is NEVER 'parity verified' unless a TV reference is explicitly marked verified (provenance column without 'UNVERIFIED')."""
import argparse
import csv
import datetime as dt
import sqlite3
import sys

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DB = "data/market_history.db"
KNOWN_PRECLOSE = ("15:15", "15:19")      # AngelOne index feed freeze, see stale_feed_watchdog (KNOWN_PRECLOSE)


def ist(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(IST)


def runs(keys):
    out = []
    for k in keys:
        m = int(k[:2]) * 60 + int(k[3:])
        if out and out[-1][1] == m - 1:
            out[-1][1] = m
        else:
            out.append([m, m])
    f = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    return [(f(a), f(b)) for a, b in out]


def classify(date, rs, n_have):
    d = dt.date.fromisoformat(date)
    if d.weekday() >= 5:
        return [("whole day", "EXPECTED_NON_TRADING (weekend)")]
    if n_have == 0:
        return [("whole day", "NO_DATA (weekday: holiday or total capture outage; check NSE holiday list)")]
    res = []
    for a, b in rs:
        if (a, b) == KNOWN_PRECLOSE:      # exact documented run only; anything else stays GENUINE (e.g. 15:29)
            res.append((f"{a}-{b}", "KNOWN_VENDOR_FREEZE (Angel index feed pre-close, recurring every session)"))
        else:
            res.append((f"{a}-{b}", "GENUINE_MISSING (cause not proven)"))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--buckets", default="09:20,09:25,09:30")
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--yahoo")
    ap.add_argument("--tv")
    ap.add_argument("--out")
    a = ap.parse_args()
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    L = []
    P = L.append
    P(f"# Candle parity diagnostic -- {a.symbol} {a.date} (read-only)\n")
    sess = a.date
    one = [r for r in db.execute("select bar_start,o,h,l,c,v,source,flags,instrument_key from market_candles "
                                 "where symbol=? and kind='INDEX' and tf='1m' and session_date_ist=? order by bar_start,source",
                                 (a.symbol, sess))]
    by = {}
    for r in one:
        by.setdefault(ist(r[0]).strftime("%H:%M"), []).append(r)
    five = {ist(r[0]).strftime("%H:%M"): r for r in db.execute(
        "select bar_start,o,h,l,c,v,source from market_candles where symbol=? and kind='INDEX' and tf='5m' and session_date_ist=?", (a.symbol, sess))}
    ykeys = {}
    if a.yahoo:
        for r in csv.DictReader(open(a.yahoo)):
            if r["time_ist"].startswith(a.date):
                ykeys[r["time_ist"][11:]] = tuple(float(r[k]) for k in ("open", "high", "low", "close"))
    tvk, tvprov = {}, {}
    if a.tv:
        for r in csv.DictReader(open(a.tv)):
            if r["time_ist"].startswith(a.date):
                tvk[r["time_ist"][11:]] = tuple(float(r[k]) for k in ("open", "high", "low", "close"))
                tvprov[r["time_ist"][11:]] = r.get("provenance", "")
    P(f"instrument_key(s): {sorted({r[8] for r in one})}; 1m sources: {sorted({r[6] for r in one})}; "
      f"minutes with >1 source: {sum(len(v) > 1 for v in by.values())}\n")
    builder = []   # per tested bucket: True/False = stored 5m equals independent aggregation (<=0.005)
    verified_tv = bool(tvk) and all("UNVERIFIED" not in p.upper() for p in tvprov.values())
    for b in a.buckets.split(","):
        h, m = map(int, b.split(":"))
        keys = [f"{h:02d}:{m + i:02d}" for i in range(5)]
        P(f"## Bucket {b} IST (1m {keys[0]}..{keys[-1]})\n")
        P("| IST | bar_start UTC | source | O | H | L | C | vol | flags |\n|---|---|---|---|---|---|---|---|---|")
        sel = []
        for k in keys:
            if k not in by:
                P(f"| {k} | MISSING | | | | | | | |")
            for r in by.get(k, []):
                P(f"| {k} | {r[0]} | {r[6]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[7]} |")
                sel.append(r)
        if len(sel) != 5:
            P(f"\n**Incomplete bucket ({len(sel)}/5 1m rows): independent aggregation not valid.**\n")
            continue
        calc = (sel[0][1], max(r[2] for r in sel), min(r[3] for r in sel), sel[-1][4])
        cols = [("calc(1m)", calc)]
        if b in five:
            builder.append(all(abs(x - y) <= 0.005 for x, y in zip(calc, five[b][1:5])))
            cols.append(("stored5m", tuple(five[b][1:5])))
        if b in ykeys:
            cols.append(("yahoo", ykeys[b]))
        if b in tvk:
            cols.append(("TV(" + ("verified" if verified_tv else "UNVERIFIED") + ")", tvk[b]))
        P("\n| field | " + " | ".join(n for n, _ in cols) + " | " + " | ".join(f"abs(calc-{n})" for n, _ in cols[1:]) + " |")
        P("|---|" + "---|" * (2 * len(cols) - 1))
        for i, f in enumerate("OHLC"):
            P(f"| {f} | " + " | ".join(f"{v[i]:.2f}" for _, v in cols) + " | " + " | ".join(f"{abs(calc[i] - v[i]):.2f}" for _, v in cols[1:]) + " |")
        prev = by.get(f"{h:02d}:{m - 1:02d}")
        if prev:
            P(f"\nprior 1m close ({h:02d}:{m - 1:02d}) = {prev[0][4]}; first 1m open = {sel[0][1]}; gap(first-open minus prior-close) = {sel[0][1] - prev[0][4]:+.2f}\n")
    exp = [f"{x // 60:02d}:{x % 60:02d}" for x in range(555, 930)]
    miss = [k for k in exp if k not in by]
    P(f"## Missing 1m intervals {a.date}: {len(miss)}/375 in-session minutes\n")
    for rng, cls in classify(a.date, runs(miss) if by else [], len(by)):
        P(f"- {rng}: {cls}")
    P("\n## Verdict\n")
    P(f"- Stored 5m == independent aggregation of the 1m rows: {sum(builder)}/{len(builder)} tested buckets" + ("" if builder else " (no stored 5m to compare)") + "; builder NOT modified.")
    P("- Any Open difference vs Yahoo/TV equal to (first 1m open - prior 1m close) is an open-convention difference, not a builder error; which convention TradingView uses is NOT established.")
    P("- TradingView parity: " + ("TV reference present and marked verified -- compare tables above." if verified_tv else "**UNVERIFIED** (reference transcribed from a screenshot, not the Data Window)."))
    txt = "\n".join(L) + "\n"
    if a.out:
        open(a.out, "w").write(txt)
        print("wrote", a.out)
    else:
        sys.stdout.write(txt)


if __name__ == "__main__":
    main()
