"""Research-only combination layer. Three positive states + NO TRADE; order flow is only counted when its inputs pass the documented data-quality bar.
No probabilities are produced (none are calibrated)."""
from __future__ import annotations

from . import orderflow as of
from .schema import to_orderflow_input
from .ticks import segments

PA_ONLY, OF_ONLY, BOTH, NONE = "PRICE ACTION CONFIRMED", "ORDER FLOW CONFIRMED", "BOTH CONFIRMED", "NO TRADE"
UNVERIFIED = "ORDER FLOW UNVERIFIED"


def of_signals(bars: list[dict]) -> list[dict]:
    """Absorption/exhaustion per contiguous gap-free segment; a signal needs bars i and i-1 with dq OK (closed, enough ticks, quote-rule majority, low unknown)."""
    res = []
    for seg in segments(bars):
        sb = []
        for i in seg:
            b = bars[i]
            try: sb.append(to_orderflow_input(b))
            except ValueError: sb = []; break
        if not sb: continue
        for k, i in enumerate(seg):
            if k < 2 or bars[i]["dq"]["status"] != "OK" or bars[seg[k - 1]]["dq"]["status"] != "OK": continue
            for fn, name in ((of.absorption_at, "ABSORPTION"), (of.exhaustion_at, "EXHAUSTION")):
                s = fn(sb, k)
                if s:
                    b = bars[i]
                    res.append({"id": f"OF-{b['symbol']}-{b['tf_sec']}-{b['t']}-{name[:3]}", "source": "orderflow", "kind": name, "t": b["t"], "idx": i, "side": s.side, "entry": s.entry,
                                "stop": s.stop, "t2": s.target, "rule": f"{name} (quote/tick-rule INFERRED aggressor volume)", "status": "CONFIRMED", "confirmed_t": b["t"] + b["tf_sec"]})
    return res


def of_usable(bars: list[dict], i: int) -> tuple[bool, list[str]]:
    b = bars[i]; r = list(b["dq"]["reasons"])
    if i and bars[i - 1]["dq"]["status"] != "OK": r.append("PREV_BAR_DQ_NOT_OK")
    if i and bars[i]["t"] - bars[i - 1]["t"] != b["tf_sec"]: r.append("MISSING_PREV_BAR")
    return (not r), r


def combine(bars: list[dict], pa_sigs: list[dict], of_sigs: list[dict]) -> list[dict]:
    """One record per bar that has a PA or OF signal. Alignment is by bar start `t` of the SAME bar list (same symbol/session/tf by construction)."""
    by_pa = {s["idx"]: s for s in pa_sigs}; by_of = {s["idx"]: s for s in of_sigs}
    out = []
    for i in sorted(set(by_pa) | set(by_of)):
        p, o = by_pa.get(i), by_of.get(i)
        ok, why = of_usable(bars, i) if bars[i].get("buy") is not None else (False, ["NO_ORDERFLOW_DATA"])
        if p and ok and o and o["side"] == p["side"]: state, ofs = BOTH, "ORDER FLOW CONFIRMS (inferred)"
        elif p: state, ofs = PA_ONLY, (UNVERIFIED if not ok else ("ORDER FLOW OPPOSES" if o else "NO ORDER FLOW CONFIRMATION"))
        elif o and ok: state, ofs = OF_ONLY, "ORDER FLOW CONFIRMS (inferred)"
        else: state, ofs = NONE, UNVERIFIED
        out.append({"t": bars[i]["t"], "idx": i, "state": state, "of_status": ofs, "of_unverified_reasons": [] if ok else why,
                    "pa_id": p["id"] if p else None, "of_id": o["id"] if o else None})
    return out
