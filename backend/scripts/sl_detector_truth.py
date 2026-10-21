"""Record the REAL stop of a signal (entered by the project owner after the prediction exists).   python scripts/sl_detector_truth.py <signal id> <true stop>   |   python scripts/sl_detector_truth.py --report"""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from app import sl_detector as D
if sys.argv[1:] == ["--report"]: print(json.dumps(D.report(), indent=1)); sys.exit(0)
if len(sys.argv) != 3: sys.exit(__doc__)
r = D.record_truth(sys.argv[1], float(sys.argv[2])); print("recorded", r["signal_id"], r["true_sl"], r["hash"][:10])
