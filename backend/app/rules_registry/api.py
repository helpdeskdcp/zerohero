"""Read-only API: GET /api/rules/inventory (scan result, cached 60 s), GET /api/rules/report (markdown). Kill switch: RULES_REGISTRY=0."""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from . import report, scan

router = APIRouter(prefix="/api/rules", tags=["rules-registry"])
_cache: dict = {"t": 0.0, "v": None}


def _get():
    if os.environ.get("RULES_REGISTRY", "1") == "0": raise HTTPException(503, "disabled (RULES_REGISTRY=0)")
    if time.time() - _cache["t"] > 60 or _cache["v"] is None:
        _cache.update(t=time.time(), v=scan.scan())
    return _cache["v"]


@router.get("/inventory")
def inventory(): return _get()


@router.get("/report", response_class=PlainTextResponse)
def md(): return report.render(_get())
