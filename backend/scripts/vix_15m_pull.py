"""India VIX 15-minute candles (Upstox v3, month by month) -> data/historical/vix/india_vix_15m.csv  (research only)."""
import os, sys, time
from datetime import date, timedelta
from urllib.parse import quote
import pandas as pd
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.connectors import upstox as U
K = "NSE_INDEX|India VIX"; rows = []; d = date(2022, 1, 1); END = date(2026, 10, 23)
while d <= END:
    nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1); b = min(nxt - timedelta(days=1), END)
    for a in range(4):
        r = U._authed_get(f"/v3/historical-candle/{quote(K, safe='')}/minutes/15/{b.isoformat()}/{d.isoformat()}")
        if r.status_code == 200: rows += (r.json().get("data") or {}).get("candles", []); break
        time.sleep(2 * (a + 1))
    else: print("FAIL", d)
    d = nxt; time.sleep(0.25)
df = pd.DataFrame(rows, columns=["timestamp", "o", "h", "l", "c", "v", "oi"]).drop_duplicates("timestamp").sort_values("timestamp")
df.to_csv(os.path.join(HERE, "data", "historical", "vix", "india_vix_15m.csv"), index=False); print(len(df), df.timestamp.iloc[0], df.timestamp.iloc[-1])
