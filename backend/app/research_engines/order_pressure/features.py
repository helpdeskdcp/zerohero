"""
Causal per-bar feature vector for the next-candle model.

Everything here uses ONLY bars[0..i] (and precomputed per-bar pressure for the
same range). No bar i+1, no future.

Streams (each independent, per the spec):
  SPOT   -- always present
  CE     -- present only when an option leg is time-aligned to this bar
  PE     -- ditto
Missing option streams -> their features are 0.0 and a `has_ce` / `has_pe` flag
is 0. Nothing is imputed with a fake value.

ENH (operator-approved 2026-09-08):
  * oi_confirm            net pressure sign agrees with the dominant price/OI
                          candidate -> +1 (confirmed) / -1 (contradicted) / 0
  * cepe_pressure_spread  net_CE - net_PE  and buyer_CE - buyer_PE  (genuine
                          directional option demand vs two-sided indecision)
  * mtf_alignment/conflict 1m vs 3m vs 5m net-pressure agreement
"""
from __future__ import annotations

from .config import merged
from .formulas import pressure, atr
from .series import multi_window

_LB = None  # filled from cfg


def precompute_pressure(bars: list[dict], cfg: dict | None = None) -> list[dict]:
    """Per-bar pressure for the whole series (one pass, causal by construction)."""
    c = cfg or merged()
    out = []
    prior_dir = 0
    vols: list[float] = []
    for b in bars:
        v = b.get("v_delta") if b.get("v_delta") is not None else b.get("v")
        recent = (sum(vols[-20:]) / len(vols[-20:])) if vols else None
        p = pressure(b, recent_avg_vol=recent, prior_dir=prior_dir, cfg=c)
        out.append(p)
        prior_dir = 1 if b["c"] > b["o"] else (-1 if b["c"] < b["o"] else 0)
        if v:
            vols.append(v)
    return out


def oi_price_state(price_delta: float, oi: float | None, oi_change: float | None,
                   atr_val: float, cfg: dict) -> dict:
    """The four CANDIDATES (not certainties). Only fires when both the price move
    and the OI move clear their minimum thresholds."""
    st = {"long_building": 0, "short_building": 0, "short_covering": 0,
          "long_unwinding": 0, "state": "NONE"}
    if oi is None or oi_change is None or atr_val <= cfg["eps"] or not oi:
        return st
    price_up = price_delta >= cfg["price_move_min_atr"] * atr_val
    price_dn = price_delta <= -cfg["price_move_min_atr"] * atr_val
    oi_up = (oi_change / oi) * 100.0 >= cfg["oi_change_min_pct"]
    oi_dn = (oi_change / oi) * 100.0 <= -cfg["oi_change_min_pct"]
    if price_up and oi_up:
        st["long_building"] = 1; st["state"] = "LONG_BUILDING"
    elif price_dn and oi_up:
        st["short_building"] = 1; st["state"] = "SHORT_BUILDING"
    elif price_up and oi_dn:
        st["short_covering"] = 1; st["state"] = "SHORT_COVERING"
    elif price_dn and oi_dn:
        st["long_unwinding"] = 1; st["state"] = "LONG_UNWINDING"
    return st


def _stream_block(bars, pr, i, cfg, prefix):
    """dynamics + current-bar pressure for one stream, ending at bar i."""
    net_series = [pr[j]["net"] for j in range(i + 1)]
    price_series = [bars[j]["c"] for j in range(i + 1)]
    mw = multi_window(net_series, cfg=cfg, price_series=price_series,
                      pressure_series=net_series)
    f = {
        f"{prefix}_buyer": pr[i]["buyer"],
        f"{prefix}_seller": pr[i]["seller"],
        f"{prefix}_net": pr[i]["net"],
        f"{prefix}_wnet": mw["fused"]["weighted_net"],
        f"{prefix}_slope": mw["fused"]["slope"],
        f"{prefix}_persistence": mw["fused"]["persistence"],
        f"{prefix}_reversal": mw["fused"]["any_reversal"],
        f"{prefix}_divergence": mw["fused"]["any_divergence"],
    }
    for lb in cfg["lookbacks"]:
        f[f"{prefix}_net_lb{lb}"] = mw[lb]["weighted_net"]
        f[f"{prefix}_slope_lb{lb}"] = mw[lb]["slope"]
        f[f"{prefix}_accel_lb{lb}"] = mw[lb]["acceleration"]
    return f, mw


def mtf_fusion(net_1m: float, net_3m: float, net_5m: float, cfg: dict | None = None) -> dict:
    c = cfg or merged()
    tol = c["tf_align_tol"]
    signs = [1 if x > tol else (-1 if x < -tol else 0) for x in (net_1m, net_3m, net_5m)]
    nz = [s for s in signs if s != 0]
    aligned = len(nz) >= 2 and len(set(nz)) == 1
    conflict = 1 in signs and -1 in signs
    return {
        "mtf_net_1m": round(net_1m, 3), "mtf_net_3m": round(net_3m, 3), "mtf_net_5m": round(net_5m, 3),
        "mtf_aligned": int(aligned),
        "mtf_align_dir": (nz[0] if aligned else 0),
        "mtf_conflict": int(conflict),
        "mtf_spread": round(max(net_1m, net_3m, net_5m) - min(net_1m, net_3m, net_5m), 3),
    }


def build_row(spot_bars, spot_pr, i, cfg, *, ce=None, pe=None, mtf=None):
    """Full causal feature dict for spot bar `i`. ce/pe = (bars, pressure) tuples
    already sliced to the same clock as `spot_bars` (or None)."""
    c = cfg or merged()
    a = atr(spot_bars[max(0, i - c["atr_window"]): i + 1], c["atr_window"], c) or c["eps"]

    row, sp_mw = _stream_block(spot_bars, spot_pr, i, c, "spot")
    row["atr"] = round(a, 5)
    row["ret_1"] = round((spot_bars[i]["c"] - spot_bars[i - 1]["c"]) / a, 4) if i >= 1 else 0.0
    row["close_loc"] = spot_pr[i]["geo_close_location"]
    row["body_ratio"] = spot_pr[i]["geo_body_ratio"]

    # ---- CE / PE streams ----
    for tag, pair in (("ce", ce), ("pe", pe)):
        if pair and pair[0] and len(pair[0]) > i and i >= 1:
            ob, opr = pair
            blk, _ = _stream_block(ob, opr, i, c, tag)
            row.update(blk)
            row[f"has_{tag}"] = 1
            oi = ob[i].get("oi")
            oic = ob[i].get("oi_change")
            row[f"{tag}_oi"] = oi if oi is not None else 0.0
            row[f"{tag}_oi_chg"] = oic if oic is not None else 0.0
            row[f"{tag}_oi_chg_pct"] = round((oic / oi) * 100.0, 3) if (oi and oic is not None) else 0.0
            pd = ob[i]["c"] - ob[i - 1]["c"]
            oa = atr(ob[max(0, i - c["atr_window"]): i + 1], c["atr_window"], c) or c["eps"]
            st = oi_price_state(pd, oi, oic, oa, c)
            for k, v in st.items():
                if k != "state":
                    row[f"{tag}_{k}"] = v
            # ENH oi_confirm for this leg
            net = opr[i]["net"]
            dom = 1 if (st["long_building"] or st["short_covering"]) else (
                -1 if (st["short_building"] or st["long_unwinding"]) else 0)
            row[f"{tag}_oi_confirm"] = (1 if (dom and (net > 0) == (dom > 0)) else
                                       (-1 if dom else 0))
        else:
            for k in ("buyer", "seller", "net", "wnet", "slope", "persistence", "reversal",
                      "divergence", "oi", "oi_chg", "oi_chg_pct", "long_building",
                      "short_building", "short_covering", "long_unwinding", "oi_confirm"):
                row[f"{tag}_{k}"] = 0.0
            for lb in c["lookbacks"]:
                row[f"{tag}_net_lb{lb}"] = 0.0
                row[f"{tag}_slope_lb{lb}"] = 0.0
                row[f"{tag}_accel_lb{lb}"] = 0.0
            row[f"has_{tag}"] = 0

    # ENH CE/PE pressure spread
    row["cepe_net_spread"] = round(row.get("ce_net", 0.0) - row.get("pe_net", 0.0), 3)
    row["cepe_buyer_spread"] = round(row.get("ce_buyer", 0.0) - row.get("pe_buyer", 0.0), 3)
    row["cepe_both_bid"] = int(row.get("ce_net", 0) > 10 and row.get("pe_net", 0) > 10)  # indecision

    # ---- multi-TF ----
    row.update(mtf or {"mtf_net_1m": row["spot_net"], "mtf_net_3m": 0.0, "mtf_net_5m": 0.0,
                       "mtf_aligned": 0, "mtf_align_dir": 0, "mtf_conflict": 0, "mtf_spread": 0.0})

    # ENH spot oi_confirm from multi-TF: pressure aligned across TFs AND trending
    row["spot_mtf_confirm"] = int(row["mtf_aligned"] and abs(row["spot_slope"]) > 0.5
                                  and (row["spot_net"] > 0) == (row["mtf_align_dir"] > 0))
    return row


def feature_names(cfg: dict | None = None) -> list[str]:
    """Stable ordered feature list (excludes bookkeeping keys like atr)."""
    c = cfg or merged()
    base = ["spot_buyer", "spot_seller", "spot_net", "spot_wnet", "spot_slope",
            "spot_persistence", "spot_reversal", "spot_divergence", "ret_1",
            "close_loc", "body_ratio", "spot_mtf_confirm"]
    for lb in c["lookbacks"]:
        base += [f"spot_net_lb{lb}", f"spot_slope_lb{lb}", f"spot_accel_lb{lb}"]
    for tag in ("ce", "pe"):
        base += [f"{tag}_buyer", f"{tag}_seller", f"{tag}_net", f"{tag}_wnet", f"{tag}_slope",
                 f"{tag}_persistence", f"{tag}_reversal", f"{tag}_divergence",
                 f"{tag}_oi_chg_pct", f"{tag}_long_building", f"{tag}_short_building",
                 f"{tag}_short_covering", f"{tag}_long_unwinding", f"{tag}_oi_confirm",
                 f"has_{tag}"]
        for lb in c["lookbacks"]:
            base += [f"{tag}_net_lb{lb}", f"{tag}_slope_lb{lb}"]
    base += ["cepe_net_spread", "cepe_buyer_spread", "cepe_both_bid",
             "mtf_net_1m", "mtf_net_3m", "mtf_net_5m", "mtf_aligned", "mtf_align_dir",
             "mtf_conflict", "mtf_spread"]
    return base
