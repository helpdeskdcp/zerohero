"""Small math helpers used by the local-model Claude Code test."""


def add(a, b):
    """Return the sum of a and b."""
    return a + b


def is_prime(n):
    """Return True if n is a prime number (n > 1), else False."""
    if n < 2:
        return False
    for d in range(2, int(n ** 0.5) + 1):
        if n % d == 0:
            return False
    return True
