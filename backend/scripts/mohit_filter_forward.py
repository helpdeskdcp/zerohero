"""Nightly read-only job: tag new INSTITUTION_TRADER calls ACCEPT / REJECT with the frozen Mohit-profile rule and refresh the forward summary. No order, no scoring change. python scripts/mohit_filter_forward.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.arena import mohit_forward as F
s = F.run(); print(json.dumps({"tagged": s["tagged"], "new": s["new_tags"], "forward_accept": s["groups"]["FORWARD/ACCEPT"], "forward_reject": s["groups"]["FORWARD/REJECT"], "gate": s["gate"]}))
