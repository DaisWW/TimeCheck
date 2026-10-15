from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any


DATE_RE = re.compile(
    r"(?P<year>\d{4}|\d{2})(?P<separator>[-/])(?P<month>\d{1,2})"
    r"(?P=separator)(?P<day>\d{1,2})(?:\s+星期(?P<weekday>[一二三四五六日天]))?"
)
TIME_RE = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?")
DATETIME_RE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2} \d{1,2}:\d{2}(?::\d{2})?")
SHIFT_RE = re.compile(r"(?:\S+\s+)?(?P<start>\d{1,2}:\d{2})\s*-\s*(?P<end>\d{1,2}:\d{2})")


def parse_time(value: str) -> time:
    match = TIME_RE.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"Invalid time: {value!r}")
    return time(int(match.group("hour")), int(match.group("minute")), int(match.group("second") or 0))


def parse_shift_times(value: str) -> tuple[time, time]:
    match = SHIFT_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"班次无法解析，应为 A 09:30-18:30：{value!r}")
    start, end = parse_time(match.group("start")), parse_time(match.group("end"))
    if end <= start:
        raise ValueError(f"当前仅支持当天结束的单段班次：{value!r}")
    return start, end


def parse_date_text(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date() if value.time() == time.min and value.tzinfo is None else None
    if isinstance(value, date):
        return value
    if value is None:
        return None

    match = DATE_RE.fullmatch(str(value).strip())
    if not match:
        return None

    year = int(match.group("year"))
    if year < 100:
        year += 2000
    try:
        result = date(year, int(match.group("month")), int(match.group("day")))
    except ValueError:
        return None
    weekday = match.group("weekday")
    if weekday and "一二三四五六日".index("日" if weekday == "天" else weekday) != result.weekday():
        return None
    return result


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is None and not value.microsecond else None
    if value is None:
        return None

    text = str(value).strip()
    if not DATETIME_RE.fullmatch(text):
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def combine_date_time(work_date: date, value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return parse_datetime(value)
    if isinstance(value, time):
        if value.tzinfo is not None or value.microsecond:
            return None
        return datetime.combine(work_date, value)
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    next_day = text.startswith("次日")
    if next_day:
        text = text.removeprefix("次日").strip()
    if TIME_RE.fullmatch(text) is None:
        return None
    try:
        clock = parse_time(text)
    except ValueError:
        return None
    result = datetime.combine(work_date, clock)
    if next_day:
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
