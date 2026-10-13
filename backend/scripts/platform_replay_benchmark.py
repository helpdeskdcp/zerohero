#!/usr/bin/env python3
"""Replay REAL stored Dhan ticks (read-only) through TickGate -> CandleEngine1s -> BufferedCandleWriter.

Reports integrity counters (duplicates / late / outside-session / gaps) and measured throughput,
RSS and write latency. Writes ONLY to a temporary SQLite file (never a production DB).
Usage: python scripts/platform_replay_benchmark.py [--limit N] [--no-writer]
"""
import argparse
import os
import resource
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.platform.candles.engine_1s import CandleEngine1s  # noqa: E402
from app.platform.marketdata.arbiter import TickGate  # noqa: E402
from app.platform.marketdata.types import Tick  # noqa: E402
from app.platform.persistence.buffered_writer import BufferedCandleWriter  # noqa: E402

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "dhan_orderflow_ticks.db")
MCX_IDS = {"570750", "569900"}


def _epoch(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-writer", action="store_true")
    a = ap.parse_args()
    conn = sqlite3.connect(f"file:{os.path.abspath(SRC)}?mode=ro", uri=True)
    q = "SELECT received_ts, security_id, ltp, ltq, ltt FROM classified_ticks ORDER BY id" + (f" LIMIT {a.limit}" if a.limit else "")
    rows = conn.execute(q).fetchall()
    ticks = []
    for rts, sid, ltp, ltq, ltt in rows:
        inst = ("MCX:" if str(sid) in MCX_IDS else "NSE:") + str(sid)
        ticks.append(Tick("DHAN", inst, float(ltp), float(ltt) if ltt else None, _epoch(rts), qty=ltq))
    tmp = tempfile.mkdtemp(prefix="replay_")
    w = None if a.no_writer else BufferedCandleWriter(os.path.join(tmp, "replay.db"))
    gate, eng = TickGate(lambda: "DHAN"), CandleEngine1s()
    t0 = time.perf_counter()
    n_candles = 0
    for t in ticks:
        r = gate.accept(t)
        if r.accepted:
            for c in eng.add(t, r.reset_volume_baseline):
                n_candles += 1
                if w:
                    w.offer(c)
    last = max(t.recv_ts for t in ticks)
    for c in eng.flush(last + 10):
        n_candles += 1
        if w:
            w.offer(c)
    t_pipe = time.perf_counter() - t0
    written = 0
    if w:
        t1 = time.perf_counter(); written = w.flush_sync(); t_w = time.perf_counter() - t1
        w.write_gaps(eng.gaps)
    print(f"ticks replayed: {len(ticks):,} in {t_pipe:.2f}s -> {len(ticks) / t_pipe:,.0f} ticks/s (gate+engine, in-process, single thread)")
    print("gate counters:", dict(gate.counters))
    print("engine counters:", eng.counters, "| gap events:", len(eng.gaps))
    print(f"1s candles: {n_candles:,}  (ticks/candle {len(ticks) / max(1, n_candles):.2f})")
    if w:
        st = w.status()
        print(f"writer: written={written:,} in {t_w:.2f}s ({written / max(t_w, 1e-9):,.0f} rows/s), p50={st['write_latency_ms_p50']:.1f}ms p95={st['write_latency_ms_p95']:.1f}ms, "
              f"file={os.path.getsize(w.path) / 1e6:.1f} MB ({os.path.getsize(w.path) / max(1, written):.0f} B/row incl. indexes)")
    print(f"peak RSS: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB")
    secs = {}
    for t in ticks:
        secs.setdefault((t.instrument, int(t.source_ts or 0)), 0)
        secs[(t.instrument, int(t.source_ts or 0))] += 1
    same_sec = sum(1 for v in secs.values() if v > 1)
    print(f"seconds with >1 tick: {same_sec:,} of {len(secs):,} ({100 * same_sec / len(secs):.1f}%) -> second-resolution source_ts")


if __name__ == "__main__":
    main()
