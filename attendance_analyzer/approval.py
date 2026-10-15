from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re


LEAVE_KEYWORDS = ("请假", "年假", "事假", "病假", "婚假", "调休", "外出", "出差")
BUSINESS_ATTENDANCE_TYPES = frozenset({"外出", "出差"})
APPROVAL_DATE = r"(?:(?:\d{4}-)?\d{1,2}-\d{1,2}|(?:\d{4}/)?\d{1,2}/\d{1,2})"
APPROVAL_INTERVAL_RE = re.compile(
    r"(?P<kind>加班|补卡申请|补卡|"
    + "|".join(LEAVE_KEYWORDS)
    + r")[:：\s]*"
    rf"(?P<start_date>{APPROVAL_DATE})\s*"
    r"(?P<start_time>\d{1,2}:\d{2})\s*到\s*"
    rf"(?P<end_date>{APPROVAL_DATE})\s*"
    r"(?P<end_time>\d{1,2}:\d{2})"
    r"(?:\s*\d+(?:\.\d+)?(?:小时|分钟|天))?"
)


@dataclass(frozen=True)
class OvertimeApprovalInterval:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class LeaveApprovalInterval:
    kind: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class ApprovalParseIssue:
    kind: str
    message: str
    blocks_summary: bool = True


def parse_overtime_approval_intervals(text: str, year: int) -> list[OvertimeApprovalInterval]:
    overtime, _, _ = _parse_approval_text(text, year)
    return overtime


def parse_leave_approval_intervals(text: str, year: int) -> list[LeaveApprovalInterval]:
    _, leave, _ = _parse_approval_text(text, year)
    return leave


def approval_parse_issues(text: str, year: int) -> list[ApprovalParseIssue]:
    _, _, issues = _parse_approval_text(text, year)
    return issues


def interval_union_minutes(intervals: list[OvertimeApprovalInterval | LeaveApprovalInterval]) -> int:
    if not intervals:
        return 0

    sorted_intervals = sorted((interval.start, interval.end) for interval in intervals)
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted_intervals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))

    return sum(int((end - start).total_seconds() // 60) for start, end in merged)


def overlapping_interval_minutes(
    start: datetime,
    end: datetime,
    intervals: list[OvertimeApprovalInterval | LeaveApprovalInterval],
) -> int:
    if end <= start:
        return 0
    overlaps = [
        OvertimeApprovalInterval(start=max(start, interval.start), end=min(end, interval.end))
        for interval in intervals
        if interval.start < end and interval.end > start
    ]
    return interval_union_minutes(overlaps)


def _parse_approval_text(
    text: str, year: int
) -> tuple[list[OvertimeApprovalInterval], list[LeaveApprovalInterval], list[ApprovalParseIssue]]:
    overtime: list[OvertimeApprovalInterval] = []
    leave: list[LeaveApprovalInterval] = []
    issues: list[ApprovalParseIssue] = []
    text = text.strip()

    for part in re.split(r"[，,；;\n]+", text):
        part = part.strip()
        if not part:
            continue
        interval_match = APPROVAL_INTERVAL_RE.fullmatch(part)
        if interval_match is None:
            issues.append(
                ApprovalParseIssue(
                    kind="未知或格式错误",
                    message=f"审批无法完整解析：{part!r}。应为已知审批类型 + MM-DD HH:MM到MM-DD HH:MM，可附数字时长；多项用逗号、分号或换行分隔。",
                )
            )
            continue

        kind = interval_match.group("kind")
        try:
            start = _parse_datetime(
                interval_match.group("start_date"), interval_match.group("start_time"), year
            )
            end = _parse_datetime(
                interval_match.group("end_date"), interval_match.group("end_time"), year
            )
        except ValueError:
            issues.append(
                ApprovalParseIssue(
                    kind=kind,
                    message=f"{kind}审批的日期或时间无效，未参与自动计算。",
                )
            )
            continue

        supplement = kind in {"补卡", "补卡申请"}
        if end < start or (end == start and not supplement):
            issues.append(ApprovalParseIssue(
                kind=kind,
                message=f"{kind}审批结束时间必须晚于开始时间；不自动补一天。跨午夜请写次日日期，跨年请写完整年份。",
            ))
            continue
        if supplement:
            issues.append(ApprovalParseIssue(
                kind=kind,
                message="存在补卡申请；当前不会自动用审批替代原始打卡，请 HR 确认后补卡并重新统计。",
                blocks_summary=False,
            ))
            continue
        if kind == "加班":
            overtime.append(OvertimeApprovalInterval(start=start, end=end))
        else:
            leave.append(LeaveApprovalInterval(kind=kind, start=start, end=end))

    return overtime, leave, issues


def _parse_datetime(date_text: str, time_text: str, year: int) -> datetime:
    parts = re.split(r"[-/]", date_text)
    if len(parts) == 3:
        year = int(parts.pop(0))
    month_text, day_text = parts
    hour_text, minute_text = time_text.split(":")
    return datetime(year, int(month_text), int(day_text), int(hour_text), int(minute_text))
