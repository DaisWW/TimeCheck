from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any


DATE_PREFIX_RE = re.compile(r"(?P<year>\d{2,4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})")
TIME_RE = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})")


def parse_time(value: str) -> time:
    match = TIME_RE.search(str(value).strip())
    if not match:
        raise ValueError(f"Invalid time: {value!r}")
    return time(int(match.group("hour")), int(match.group("minute")))


def parse_date_text(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None

    match = DATE_PREFIX_RE.search(str(value).strip())
    if not match:
        return None

    year = int(match.group("year"))
    if year < 100:
        year += 2000
    try:
        return date(year, int(match.group("month")), int(match.group("day")))
    except ValueError:
        return None


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def combine_date_time(work_date: date, value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    match = TIME_RE.search(text)
    if not match:
        return None
    try:
        clock = time(int(match.group("hour")), int(match.group("minute")))
    except ValueError:
        return None
    result = datetime.combine(work_date, clock)
    if "次日" in text:
        result += timedelta(days=1)
    return result


def combine_with_possible_next_day(work_date: date, clock: time, after: datetime | None = None) -> datetime:
    value = datetime.combine(work_date, clock)
    if after is not None and value < after:
        return value + timedelta(days=1)
    return value


def round_minutes(minutes: int, unit: int, mode: str) -> int:
    if unit <= 1 or minutes <= 0:
        return max(minutes, 0)

    mode = mode.lower()
    if mode == "floor":
        return minutes // unit * unit
    if mode == "ceil":
        return ((minutes + unit - 1) // unit) * unit
    if mode == "nearest":
        return int(round(minutes / unit) * unit)
    raise ValueError(f"Unsupported rounding mode: {mode}")


def minutes_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


def fmt_dt(value: datetime | None) -> str:
    return "" if value is None else value.strftime("%Y-%m-%d %H:%M")


def fmt_time(value: datetime | None) -> str:
    return "" if value is None else value.strftime("%H:%M")
