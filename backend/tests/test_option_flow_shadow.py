import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.autoscalp import option_flow_shadow as OFS


def _bars(day="2026-10-22", spot0=22600.0):
    t0 = pd.Timestamp(f"{day} 09:15", tz="Asia/Kolkata").tz_convert("UTC"); n = 300; rng = np.random.default_rng(1)
    ts = np.array([int(t0.timestamp()) + 60 * i for i in range(n)], dtype=np.int64); c = spot0 + np.cumsum(rng.normal(0, 1.5, n))
    return {"t": ts, "o": np.r_[c[0], c[:-1]], "h": c + 1, "l": c - 1, "c": c, "v": np.zeros(n)}, float(ts[-1] + 61)


def _hist(path, bars, with_quotes=True):
    c = sqlite3.connect(path); c.execute("CREATE TABLE quote_snapshots (received_ts TEXT, symbol TEXT, kind TEXT, expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT, bid REAL, ask REAL, ltp REAL, oi REAL)")
    c.execute("INSERT INTO quote_snapshots VALUES ('2026-10-22T05:00:00.0Z','NIFTY','OPTION','27OCT2026',22600,'CE','2026-10-22',1,2,1.5,1)")
    if with_quotes:
        spot = dict(zip(bars["t"], bars["c"]))
        for off in range(0, 300 * 60, 20):
            ts = int(bars["t"][0]) + off; s = spot[int(bars["t"][0]) + (off // 60) * 60]; K = round(s / 50) * 50
            for k in (K - 100, K - 50, K, K + 50, K + 100):
                for ot, mid in (("CE", max(1.0, s - k) + 40), ("PE", max(1.0, k - s) + 40)):
                    c.execute("INSERT INTO quote_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", (datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z"), "NIFTY", "OPTION", "27OCT2026", float(k), ot, "2026-10-22", mid - 0.25, mid + 0.25, mid, 1000.0))
    c.commit(); c.close()


def test_logs_ok_and_no_quotes_dedupes_and_fills_forward(tmp_path):
    b, now = _bars(); h = str(tmp_path / "h.db"); out = str(tmp_path / "o.db"); _hist(h, b); prov = lambda sym, kind, until, days=6: {k: v[b["t"] + 60 <= until] for k, v in b.items()}
    OFS._last.clear(); r = OFS.observe("NIFTY", now - 1800 - 300, prov, h, out)
    assert r["status"] == "OK" and np.isfinite(r["F3m"]) and abs(r["spread_w"] - 1.0) < 1e-9
    assert OFS.observe("NIFTY", now - 1800 - 300, prov, h, out) is None                                      # same bar twice -> not logged again
    OFS.observe("NIFTY", now, prov, h, out)                                                                  # 30 min later: first row gets its forward return, newest row logged
    c = sqlite3.connect(out); rows = c.execute("SELECT status, fwd30_bp, fwd15_bp FROM flow_live ORDER BY key_ts").fetchall(); assert len(rows) == 2 and rows[0][1] is not None and rows[0][2] is not None and rows[1][1] is None
    assert OFS.observe("BANKNIFTY", now, prov, h, out) is None
    h2 = str(tmp_path / "h2.db"); _hist(h2, b, with_quotes=False); OFS._last.clear(); r2 = OFS.observe("NIFTY", now, prov, h2, str(tmp_path / "o2.db")); assert r2["status"] == "NO_QUOTES"


def test_forward_report_rule(tmp_path):
    out = str(tmp_path / "f.db"); c = sqlite3.connect(out); c.executescript(OFS._SCHEMA); rng = np.random.default_rng(3)
    for i in range(120):
        f = rng.normal(); c.execute("INSERT INTO flow_live (symbol, bar_start, key_ts, status, spot, F3m, F3c, F3z, logged_ts, fwd30_bp) VALUES ('NIFTY', ?, ?, 'OK', 22600, ?, ?, ?, 0, ?)", (f"2026-10-{(i // 20) + 1:02d}T05:00:00Z", i, f, f, f, f * 3 + rng.normal(0, 3)))
    c.commit(); c.close(); r = OFS.forward_report(out)["symbols"]["NIFTY"]
    assert r["evaluated"] == 120 and r["auc_F3m"] > 0.6 and r["conclusion_allowed"] is False and "tercile_mean_30m_bp" in r              # strong planted signal visible, but no conclusion before 60 sessions / 3000 points
