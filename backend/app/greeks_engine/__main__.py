"""`python -m app.greeks_engine [--underlying NIFTY] [--expiry DDMMMYYYY] [--as-of ISO]`"""
import argparse
import pprint

from .engine import GreeksEngine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--expiry", default=None)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    eng = GreeksEngine()
    if a.status:
        pprint.pprint(eng.status())
        return
    res = eng.run_once(a.underlying, [a.expiry] if a.expiry else None, a.as_of, mode="ONCE")
    pprint.pprint(res)
    latest = eng.latest(a.underlying, a.expiry)
    if latest:
        keep = ("as_of_ts", "quality", "coverage_pct", "stale_sec", "underlying_price",
                "net_delta_exp", "net_gamma_exp", "net_vega_exp", "pcr_oi",
                "oi_weighted_iv", "gamma_conc_strike", "gamma_conc_pct")
        pprint.pprint({k: latest.get(k) for k in keep})


if __name__ == "__main__":
    main()
