"""Day-level bootstrap comparison of two participants' net P&L (rupees per trading day). 'DRAW' when the 95% interval of the difference (A - B) contains 0."""
from __future__ import annotations

import random
from datetime import datetime, timezone


def daily_net(results: list[dict]) -> dict[str, float]:
    d: dict[str, float] = {}
    for r in results:
        if r.get("status") != "SETTLED": continue
        k = datetime.fromtimestamp(r["fill_t"] + 19800, tz=timezone.utc).strftime("%Y-%m-%d"); d[k] = d.get(k, 0.0) + r.get("net_rs_1lot", r["net_rs"])
    return d


def compare(a: list[dict], b: list[dict], n_boot: int = 2000, seed: int = 11) -> dict:
    da, db = daily_net(a), daily_net(b); days = sorted(set(da) | set(db))
    if len(days) < 5: return {"verdict": "INSUFFICIENT_DAYS", "days": len(days)}
    diffs = [da.get(d, 0.0) - db.get(d, 0.0) for d in days]; total = sum(diffs)
    rnd = random.Random(seed); boots = sorted(sum(rnd.choice(diffs) for _ in diffs) for _ in range(n_boot))
    lo, hi = boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot) - 1]
    verdict = "DRAW" if lo <= 0 <= hi else ("A_AHEAD" if total > 0 else "B_AHEAD")
    return {"verdict": verdict, "days": len(days), "diff_total_rs": total, "ci95_rs": [lo, hi]}
