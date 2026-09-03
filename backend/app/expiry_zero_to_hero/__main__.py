"""
CLI:  python -m app.expiry_zero_to_hero replay        # 03-Sep-2026 SENSEX reconstruction
      python -m app.expiry_zero_to_hero collect SENSEX 10SEP2026 2026-09-10
"""
import json
import sys


def _sdk():
    from ..connectors.angelone import _market_sdk
    s = _market_sdk(require_auth=True)
    if not s:
        print("AngelOne SDK not authenticated (need ANGEL_* env). Aborting.", file=sys.stderr)
        raise SystemExit(2)
    return s


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = argv[0]
    if cmd == "replay":
        from . import replay
        out = replay.run(_sdk())
        print(json.dumps(out, indent=1, default=str))
    elif cmd == "collect" and len(argv) >= 4:
        from .data_collector import ExpiryDataCollector
        idx, exp, date = argv[1], argv[2], argv[3]
        out = ExpiryDataCollector(_sdk()).collect_window(idx, exp, date)
        print(json.dumps(out["meta"], indent=1, default=str))
        print(f"index_bars={len(out['index_bars'])} option_bars={len(out['option_bars'])}")
    else:
        print(__doc__)
        raise SystemExit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
