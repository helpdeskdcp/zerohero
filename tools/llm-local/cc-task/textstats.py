"""Text statistics helpers."""


def char_count(text):
    """Return the number of non-whitespace characters in text."""
    return sum(1 for c in text if not c.isspace())


# TODO: implement word_frequency(text) -> dict mapping each lowercased word
# to how many times it appears. Words are whitespace-separated; surrounding
# punctuation (. , ! ? ; :) should be stripped from each word.
