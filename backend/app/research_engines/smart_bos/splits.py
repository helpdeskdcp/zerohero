"""Fixed chronological splits (chosen BEFORE looking at any result) and the OOS guard."""
import datetime as dt

TRAIN = (dt.date(2017, 6, 1), dt.date(2021, 12, 31))
VALID = (dt.date(2022, 1, 1), dt.date(2023, 12, 31))
OOS = (dt.date(2024, 1, 1), dt.date(2026, 12, 31))


def day_ord(d: dt.date) -> int:
    return d.toordinal() - 719163


def in_split(day: int, split) -> bool:
    return day_ord(split[0]) <= day <= day_ord(split[1])


def of_split(items, split, key=lambda x: x.day):
    return [x for x in items if in_split(key(x), split)]
