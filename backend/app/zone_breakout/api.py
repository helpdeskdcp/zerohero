"""/api/zone-breakout/*: read-only views of the Zone Breakout paper runner and its back-test (auth'd by the normal gate)."""
from __future__ import annotations

import json
import os

from fastapi import APIRouter

from . import data as DT, live as LV

router = APIRouter(prefix="/api/zone-breakout", tags=["zone-breakout"])
RES = os.path.join(DT.ROOT, "data", "research", "zone_breakout")


def _j(name):
    try: return json.load(open(os.path.join(RES, name)))
    except Exception: return None


@router.get("/status")
def status(limit: int = 50): return {**LV.report(limit=limit), "enabled": LV.enabled(), "indices": DT.INDICES}


@router.get("/backtest")
def backtest():
    r = _j("results.json"); c = _j("results_calibration.json")
    return {"default_config": r, "calibration": c, "spec": "data/research/zone_breakout/SPEC_2026-10-25.md"}
