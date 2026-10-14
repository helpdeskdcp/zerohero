"""LITERAL transliteration of pine/hybrid_mp_v13.pine, written statement-by-statement in the script's own order, used ONLY as an
independent check of engine.py (internal parity). It deliberately shares NO code with engine.py: scalar `var` state, explicit
series history lookups, naive loops for ta.* and request.security.

Scope / honesty: this is a second Python implementation, written from the Pine text and the TradingView-validated PyneCore
semantics for ta.pivothigh/pivotlow (left ties allowed, right ties not), ta.atr (rma seeded with SMA of true range, tr(handle_na)),
ta.highest(high[1], n). It is NOT TradingView output. Execution model = the script's own (entry at the signal close, level fills,
no end-of-day exit, SL or T4 closes the trade).
Events are emitted in the same schema as engine.run(..., want_events=True)."""
from __future__ import annotations

import datetime as dt
import math

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _na(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def run_literal(t, o, h, l, c, v, b1, cfg: dict) -> list:
    """t,o,h,l,c,v: chart bars (t = bar OPEN epoch). b1: 1-minute bars dict for request.security. cfg: Pine inputs."""
    n = len(t)
    g = lambda k, d: cfg.get(k, d)
    pivotLen, atrLen, retestBars = g("pivotLen", 5), g("atrLen", 14), g("retestBars", 8)
    retestTolATR, useVWAP, useCandleSL = g("retestTolATR", 0.20), g("useVWAP", False), g("useCandleSL", True)
    usePatternFilter, maruBodyRatio, maxWickRatio = g("usePatternFilter", False), g("maruBodyRatio", 0.85), g("maxWickRatio", 0.08)
    hybrid = g("entryMode", "Hybrid: Retest or Continuation") == "Hybrid: Retest or Continuation"
    contLB, requireMTF, minAgree, mtfLB = g("continuationLookback", 5), g("requireMTF", False), g("minAgree", 3), g("mtfLookback", 5)
    tfs = g("tfs", (3, 5, 15, 40))
    mintick, useSession = g("mintick", 0.05), g("useSession", True)
    s_open, s_close = g("session", (555, 930))                     # minute-of-day, IST (India 0915-1530 / MCX 0900-2330)
    tf_chart = g("chart_tf", 1)

    # ---------- bar-time helpers (IST) ----------
    dts = [dt.datetime.fromtimestamp(x, IST) for x in t]
    mod = [d.hour * 60 + d.minute for d in dts]
    ordn = [d.date().toordinal() for d in dts]
    wk = [d.weekday() for d in dts]                                # Mon=0
    sessionOKs = [True if not useSession else (s_open <= mod[i] < s_close and wk[i] < 5) for i in range(n)]

    # ---------- 03 request.security(f_candleDir(len)[1], lookahead_on), built naively from the 1-minute bars ----------
    def htf_dir_series(N):
        bt = b1["t"]
        bd = [dt.datetime.fromtimestamp(x, IST) for x in bt]
        buckets, order = {}, []
        for k in range(len(bt)):
            m_ = bd[k].hour * 60 + bd[k].minute
            if not (s_open <= m_ < s_close):
                continue
            key = (bd[k].date().toordinal(), (m_ - s_open) // N)
            if key not in buckets:
                buckets[key] = [b1["o"][k], b1["h"][k], b1["l"][k], b1["c"][k]]
                order.append(key)
            else:
                b = buckets[key]
                b[1] = max(b[1], b1["h"][k]); b[2] = min(b[2], b1["l"][k]); b[3] = b1["c"][k]
        dirs = {}
        for idx, key in enumerate(order):
            op, hi, lo, cl = buckets[key]
            hh = ll = None
            if idx >= mtfLB:
                hh = max(buckets[order[idx - 1 - j]][1] for j in range(mtfLB))
                ll = min(buckets[order[idx - 1 - j]][2] for j in range(mtfLB))
            if hh is not None and cl > hh:
                d = 1
            elif ll is not None and cl < ll:
                d = -1
            elif cl > op:
                d = 1
            elif cl < op:
                d = -1
            else:
                d = 0
            dirs[key] = (idx, d)
        return order, dirs

    mtf = []
    for N in tfs:
        order, dirs = htf_dir_series(N)
        col = []
        for i in range(n):
            key = (ordn[i], (mod[i] - s_open) // N)
            if key in dirs and dirs[key][0] >= 1:
                col.append(dirs[order[dirs[key][0] - 1]][1])      # f()[1] -> the previous completed higher-timeframe bar
            else:
                col.append(0)
        mtf.append(col)

    # ---------- series helpers ----------
    def tr_at(i):
        if i == 0:
            return h[0] - l[0]
        return max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))

    atr = [float("nan")] * n
    for i in range(n):
        if i == atrLen - 1:
            atr[i] = sum(tr_at(k) for k in range(atrLen)) / atrLen
        elif i >= atrLen:
            atr[i] = (atr[i - 1] * (atrLen - 1) + tr_at(i)) / atrLen

    def pivothigh(i):                                              # known at bar i, pivot bar = i - pivotLen
        cidx = i - pivotLen
        if cidx - pivotLen < 0:
            return float("nan")
        val = h[cidx]
        if all(val >= h[cidx - j] for j in range(1, pivotLen + 1)) and all(val > h[cidx + j] for j in range(1, pivotLen + 1)):
            return val
        return float("nan")

    def pivotlow(i):
        cidx = i - pivotLen
        if cidx - pivotLen < 0:
            return float("nan")
        val = l[cidx]
        if all(val <= l[cidx - j] for j in range(1, pivotLen + 1)) and all(val < l[cidx + j] for j in range(1, pivotLen + 1)):
            return val
        return float("nan")

    def highest_prev(i, L):                                        # ta.highest(high[1], L)
        return max(h[i - 1 - j] for j in range(L)) if i - L >= 0 else float("nan")

    def lowest_prev(i, L):
        return min(l[i - 1 - j] for j in range(L)) if i - L >= 0 else float("nan")

    vwap = [float("nan")] * n
    cumpv = cumv = 0.0
    for i in range(n):
        if i == 0 or ordn[i] != ordn[i - 1]:
            cumpv = cumv = 0.0
        cumpv += (h[i] + l[i] + c[i]) / 3.0 * v[i]
        cumv += v[i]
        vwap[i] = cumpv / cumv if cumv > 0 else float("nan")

    # ---------- var state ----------
    swingH = swingL = float("nan")
    structureDir = 0
    pendingDir, pendingLevel, pendingBar = 0, float("nan"), None
    active, tradeDir = False, 0
    entry = sl = R = float("nan")
    t1 = t2 = t3 = t4 = float("nan")
    done = [False] * 4
    entryBar = None
    ev = []

    for i in range(n):
        sessionOK = sessionOKs[i]
        newTradingDay = i == 0 or ordn[i] != ordn[i - 1]
        # ---- 03 MTF filters ----
        bullAgree = sum(1 for col in mtf if col[i] == 1)
        bearAgree = sum(1 for col in mtf if col[i] == -1)
        mtfLongOK = (not requireMTF) or bullAgree >= minAgree
        mtfShortOK = (not requireMTF) or bearAgree >= minAgree
        # ---- 04 structure ----
        ph, pl = pivothigh(i), pivotlow(i)
        if not _na(ph):
            swingH = ph
        if not _na(pl):
            swingL = pl
        c1 = c[i - 1] if i > 0 else float("nan")
        breakH = (not _na(swingH)) and c[i] > swingH and (not _na(c1)) and c1 <= swingH
        breakL = (not _na(swingL)) and c[i] < swingL and (not _na(c1)) and c1 >= swingL
        bullCH = breakH and structureDir == -1
        bearCH = breakL and structureDir == 1
        if breakH:
            structureDir = 1
        if breakL:
            structureDir = -1
        if breakH or breakL:
            ev.append((i, "CHOCH" if (bullCH or bearCH) else "BOS", 1 if breakH else -1))
        # ---- 05 candle patterns ----
        cRange = max(h[i] - l[i], mintick)
        cBody = abs(c[i] - o[i])
        uWick = h[i] - max(o[i], c[i])
        lWick = min(o[i], c[i]) - l[i]
        bRatio = cBody / cRange
        po, pc = (o[i - 1], c[i - 1]) if i > 0 else (float("nan"), float("nan"))
        bullEngulf = c[i] > o[i] and pc < po and o[i] <= pc and c[i] >= po
        bearEngulf = c[i] < o[i] and pc > po and o[i] >= pc and c[i] <= po
        bullMaru = c[i] > o[i] and bRatio >= maruBodyRatio and uWick / cRange <= maxWickRatio and lWick / cRange <= maxWickRatio
        bearMaru = c[i] < o[i] and bRatio >= maruBodyRatio and uWick / cRange <= maxWickRatio and lWick / cRange <= maxWickRatio
        bullHammer = c[i] > o[i] and lWick >= cBody * 2 and uWick <= cBody
        bearStar = c[i] < o[i] and uWick >= cBody * 2 and lWick <= cBody
        bullPattern = bullEngulf or bullMaru or bullHammer
        bearPattern = bearEngulf or bearMaru or bearStar
        bullCandleOK = c[i] > o[i] and ((not usePatternFilter) or bullPattern)
        bearCandleOK = c[i] < o[i] and ((not usePatternFilter) or bearPattern)
        vw = vwap[i]
        vwapLongOK = (not useVWAP) or _na(vw) or c[i] >= vw
        vwapShortOK = (not useVWAP) or _na(vw) or c[i] <= vw
        trendDir = structureDir
        allowBuyDirection, allowSellDirection = trendDir == 1, trendDir == -1
        # ---- 06 new day ----
        if newTradingDay and not active:
            pendingDir, pendingLevel, pendingBar = 0, float("nan"), None
        # ---- 08 breakout / retest / continuation ----
        tol = (0.0 if _na(atr[i]) else atr[i]) * retestTolATR
        pendingExpired = pendingDir != 0 and pendingBar is not None and i - pendingBar > retestBars
        pendingStale = pendingDir != 0 and not sessionOK
        if pendingExpired:
            ev.append((i, "PENDING_EXPIRED", pendingDir))
            pendingDir, pendingLevel, pendingBar = 0, float("nan"), None
        if pendingStale:
            ev.append((i, "PENDING_CANCELLED", pendingDir))
            pendingDir, pendingLevel, pendingBar = 0, float("nan"), None
        newBreakUp = sessionOK and breakH and (not active) and pendingDir == 0
        newBreakDn = sessionOK and breakL and (not active) and pendingDir == 0
        if newBreakUp:
            pendingDir, pendingLevel, pendingBar = 1, swingH, i
            ev.append((i, "PENDING_SET", 1, swingH))
        if newBreakDn:
            pendingDir, pendingLevel, pendingBar = -1, swingL, i
            ev.append((i, "PENDING_SET", -1, swingL))
        bullRetest = pendingDir == 1 and i > pendingBar and l[i] <= pendingLevel + tol and c[i] > pendingLevel and bullCandleOK
        bearRetest = pendingDir == -1 and i > pendingBar and h[i] >= pendingLevel - tol and c[i] < pendingLevel and bearCandleOK
        hp, lp = highest_prev(i, contLB), lowest_prev(i, contLB)
        bullContinuation = hybrid and (not _na(hp)) and c[i] > hp and bullCandleOK
        bearContinuation = hybrid and (not _na(lp)) and c[i] < lp and bearCandleOK
        buySetup, sellSetup = bullRetest or bullContinuation, bearRetest or bearContinuation
        buyFilterOK, sellFilterOK = vwapLongOK and mtfLongOK, vwapShortOK and mtfShortOK
        buySignal = sessionOK and (not active) and allowBuyDirection and buySetup and buyFilterOK
        sellSignal = sessionOK and (not active) and allowSellDirection and sellSetup and sellFilterOK
        buyRejected = bullRetest and ((not allowBuyDirection) or (not buyFilterOK))
        sellRejected = bearRetest and ((not allowSellDirection) or (not sellFilterOK))
        if buyRejected or sellRejected:
            ev.append((i, "REJECTED", 1 if buyRejected else -1))
        if buySignal or sellSignal or buyRejected or sellRejected:
            pendingDir, pendingLevel, pendingBar = 0, float("nan"), None
        # ---- 10 buy entry ----
        if buySignal:
            lowPrev = l[i - 1] if i > 0 else float("nan")
            candidateSL = (lowPrev if bullEngulf else (l[i] if (bullMaru or bullHammer) else lowPrev)) if useCandleSL else lowPrev
            candidateR = c[i] - candidateSL
            if (not _na(candidateSL)) and candidateR >= mintick:
                active, tradeDir, entry, sl, R = True, 1, c[i], candidateSL, candidateR
                t1, t2, t3, t4 = entry + R, entry + R * 2, entry + R * 3, entry + R * 4
                done = [False] * 4
                entryBar = i
                ev.append((i, "SIGNAL", 1, "RETEST" if bullRetest else "CONT", sl, R))
            else:
                ev.append((i, "SIGNAL_NO_TRADE", 1))
        # ---- 11 sell entry ----
        if sellSignal:
            highPrev = h[i - 1] if i > 0 else float("nan")
            candidateSL = (highPrev if bearEngulf else (h[i] if (bearMaru or bearStar) else highPrev)) if useCandleSL else highPrev
            candidateR = candidateSL - c[i]
            if (not _na(candidateSL)) and candidateR >= mintick:
                active, tradeDir, entry, sl, R = True, -1, c[i], candidateSL, candidateR
                t1, t2, t3, t4 = entry - R, entry - R * 2, entry - R * 3, entry - R * 4
                done = [False] * 4
                entryBar = i
                ev.append((i, "SIGNAL", -1, "RETEST" if bearRetest else "CONT", sl, R))
            else:
                ev.append((i, "SIGNAL_NO_TRADE", -1))
        # ---- 12 active trade management ----
        if active and i > entryBar:
            hitSL = (l[i] <= sl) if tradeDir == 1 else (h[i] >= sl)
            if hitSL:
                active = False
                ev.append((i, "SL_HIT", tradeDir))
            else:
                tgs = (t1, t2, t3, t4)
                for k in range(4):
                    reached = (h[i] >= tgs[k]) if tradeDir == 1 else (l[i] <= tgs[k])
                    if (not done[k]) and reached:
                        done[k] = True
                        ev.append((i, f"T{k + 1}", tradeDir))
                if done[3]:
                    active = False
                    ev.append((i, "T4_COMPLETE", tradeDir))
    return ev
