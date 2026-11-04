#!/usr/bin/env python3
"""Resolve finished ZHX signals into data/safe_ai/zhx_outcomes.jsonl and print the running summary (read-only on every market DB)."""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.arena.safe_ai import zhx_outcomes as O
r = O.resolve_all(time.time()); print(json.dumps(r)); print(json.dumps(O.summary(), indent=1))
