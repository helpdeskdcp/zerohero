"""Incremental (O(1)/update) indicators for streaming candles. Causal by construction: update() sees one closed bar
at a time and the value never changes afterwards. Parity against independent batch references is in tests."""
from __future__ import annotations


class EMA:
    def __init__(self, period: int):
        self.n, self.k, self.value, self._seed, self._cnt = period, 2.0 / (period + 1), None, 0.0, 0

    def update(self, x: float):
        if self.value is None:
            self._seed += x; self._cnt += 1
            if self._cnt == self.n:
                self.value = self._seed / self.n          # SMA seed, standard
            return self.value
        self.value += self.k * (x - self.value)
        return self.value


class RSI:
    """Wilder RSI."""
    def __init__(self, period: int = 14):
        self.n, self.prev, self.ag, self.al, self._g, self._l, self._cnt, self.value = period, None, None, None, 0.0, 0.0, 0, None

    def update(self, x: float):
        if self.prev is None:
            self.prev = x
            return None
        d = x - self.prev; self.prev = x
        g, l = max(d, 0.0), max(-d, 0.0)
        if self.ag is None:
            self._g += g; self._l += l; self._cnt += 1
            if self._cnt < self.n:
                return None
            self.ag, self.al = self._g / self.n, self._l / self.n
        else:
            self.ag = (self.ag * (self.n - 1) + g) / self.n
            self.al = (self.al * (self.n - 1) + l) / self.n
        self.value = 100.0 if self.al == 0 else 100.0 - 100.0 / (1.0 + self.ag / self.al)
        return self.value


class ATR:
    """Wilder ATR over (high, low, close)."""
    def __init__(self, period: int = 14):
        self.n, self.pc, self.value, self._sum, self._cnt = period, None, None, 0.0, 0

    def update(self, h: float, l: float, c: float):
        tr = h - l if self.pc is None else max(h - l, abs(h - self.pc), abs(l - self.pc))
        self.pc = c
        if self.value is None:
            self._sum += tr; self._cnt += 1
            if self._cnt == self.n:
                self.value = self._sum / self.n
            return self.value
        self.value = (self.value * (self.n - 1) + tr) / self.n
        return self.value


class SessionVWAP:
    """VWAP resetting each session. Needs real volume: a bar with volume None/0 is skipped and counted, never guessed."""
    def __init__(self):
        self.pv = self.v = 0.0
        self.skipped = 0
        self.day = None

    def update(self, day, typical: float, volume):
        if day != self.day:
            self.day, self.pv, self.v = day, 0.0, 0.0
        if not volume or volume <= 0:
            self.skipped += 1
            return self.value
        self.pv += typical * volume; self.v += volume
        return self.value

    @property
    def value(self):
        return self.pv / self.v if self.v > 0 else None
