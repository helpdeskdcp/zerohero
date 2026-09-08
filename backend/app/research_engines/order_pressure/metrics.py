"""Classification + trading metrics. Pure Python. `CLASSES = (UP, DOWN, INSIDE)`."""
from __future__ import annotations

import math

from .labels import CLASSES, CLASS_IDX


def _argmax(p: dict) -> str:
    return max(CLASSES, key=lambda k: p.get(k, 0.0))


def classification(pairs: list[tuple[dict, str]]) -> dict:
    """pairs: [(prob_dict, true_class)]. prob_dict has keys UP/DOWN/INSIDE summing ~1."""
    n = len(pairs)
    if not n:
        return {"n": 0}
    preds = [_argmax(p) for p, _ in pairs]
    acc = sum(1 for pr, (_, y) in zip(preds, pairs) if pr == y) / n

    per = {}
    f1s = []
    for cl in CLASSES:
        tp = sum(1 for pr, (_, y) in zip(preds, pairs) if pr == cl and y == cl)
        fp = sum(1 for pr, (_, y) in zip(preds, pairs) if pr == cl and y != cl)
        fn = sum(1 for pr, (_, y) in zip(preds, pairs) if pr != cl and y == cl)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        per[cl] = {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
                   "support": sum(1 for _, y in pairs if y == cl)}
        f1s.append(f1)

    # multiclass Brier + log loss + ECE (max-prob binned)
    brier = sum(sum((p.get(cl, 0.0) - (1.0 if y == cl else 0.0)) ** 2 for cl in CLASSES)
                for p, y in pairs) / n
    ll = -sum(math.log(max(1e-12, p.get(y, 1e-12))) for p, y in pairs) / n
    bins = [[] for _ in range(10)]
    for p, y in pairs:
        conf = max(p.get(cl, 0.0) for cl in CLASSES)
        bins[min(9, int(conf * 10))].append((conf, 1.0 if _argmax(p) == y else 0.0))
    ece = sum(len(b) / n * abs(sum(c for c, _ in b) / len(b) - sum(h for _, h in b) / len(b))
              for b in bins if b)

    # confusion matrix (rows = true, cols = pred)
    cm = {ty: {py: 0 for py in CLASSES} for ty in CLASSES}
    for pr, (_, y) in zip(preds, pairs):
        cm[y][pr] += 1

    return {
        "n": n, "accuracy": round(acc, 4), "macro_f1": round(sum(f1s) / len(f1s), 4),
        "brier": round(brier, 4), "log_loss": round(ll, 4), "ece": round(ece, 4),
        "per_class": per, "confusion": cm,
        "base_rate_accuracy": round(max(per[cl]["support"] for cl in CLASSES) / n, 4),
    }


def per_class_reliability(pairs: list[tuple[dict, str]], bins: int = 10) -> dict:
    out = {}
    for cl in CLASSES:
        pts = [(p.get(cl, 0.0), 1.0 if y == cl else 0.0) for p, y in pairs]
        tab = []
        for i in range(bins):
            lo, hi = i / bins, (i + 1) / bins
            sel = [(x, h) for x, h in pts if (lo <= x < hi) or (i == bins - 1 and x == hi)]
            if sel:
                tab.append({"bin": [round(lo, 2), round(hi, 2)], "n": len(sel),
                            "pred": round(sum(x for x, _ in sel) / len(sel), 4),
                            "actual": round(sum(h for _, h in sel) / len(sel), 4)})
        b = sum((x - h) ** 2 for x, h in pts) / len(pts) if pts else None
        out[cl] = {"brier": round(b, 4) if b is not None else None, "table": tab}
    return out


def trading(trades: list[dict]) -> dict:
    """trades: [{r_multiple, points, win(bool), mae, mfe, exit_reason}]. Points-based."""
    d = [t for t in trades if t.get("r_multiple") is not None]
    if not d:
        return {"n": 0}
    wins = [t for t in d if t["r_multiple"] > 0]
    loss = [t for t in d if t["r_multiple"] <= 0]
    gw = sum(t["r_multiple"] for t in wins)
    gl = -sum(t["r_multiple"] for t in loss)
    cum = peak = dd = 0.0
    for t in d:
        cum += t["r_multiple"]; peak = max(peak, cum); dd = min(dd, cum - peak)
    return {
        "n": len(d), "win_rate": round(len(wins) / len(d), 4),
        "expectancy_R": round(sum(t["r_multiple"] for t in d) / len(d), 4),
        "profit_factor": round(gw / gl, 3) if gl else float("inf"),
        "avg_win_R": round(gw / len(wins), 3) if wins else 0.0,
        "avg_loss_R": round(-gl / len(loss), 3) if loss else 0.0,
        "max_drawdown_R": round(dd, 3),
        "mae_R_mean": round(sum(t.get("mae", 0.0) for t in d) / len(d), 3),
        "mfe_R_mean": round(sum(t.get("mfe", 0.0) for t in d) / len(d), 3),
    }
