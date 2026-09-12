"""
Structural Break / Adaptive Model layer -- SHADOW, read-only, additive.

Nothing here executes an order, touches `app/execution/`, or mutates any
existing engine's output. See app/structural_break/drift.py for the first
piece: pure statistical drift-detection primitives.
"""
