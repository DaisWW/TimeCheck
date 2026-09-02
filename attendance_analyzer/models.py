from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class PunchRecord:
    employee_name: str
    user_id: str
    occurred_at: datetime
    result: str
    source_row: int


@dataclass(frozen=True)
class DailyAttendance:
    employee_name: str
    user_id: str
    work_date: date
    shift: str
    start_time_text: str = ""
    start_result: str = ""
    end_time_text: str = ""
    end_result: str = ""
    approval_text: str = ""
    source_row: int = 0


@dataclass(frozen=True)
class InputIssue:
    source_sheet: str
    source_row: int
    employee_name: str
    user_id: str
    work_date: date | None
    field_name: str
    raw_value: str
    message: str
    impact: str
    suggested_action: str
    issue_type: str = "解析问题"
    blocks_summary: bool = True


@dataclass(frozen=True)
class AttendanceData:
    daily_records: list[DailyAttendance]
    raw_punches: dict[tuple[str, date], list[PunchRecord]]
    input_issues: list[InputIssue] = field(default_factory=list)


@dataclass(frozen=True)
class OvertimeResult:
    employee_name: str
    user_id: str
    work_date: date
    shift: str
    first_clock_in: datetime | None
    last_clock_out: datetime | None
    standard_end: datetime | None
    dinner_break_minutes: int
    overtime_start: datetime | None
    overtime_end: datetime | None
    raw_overtime_minutes: int
    overtime_minutes: int
    approval_text: str
    start_time_text: str = ""
    start_result: str = ""
    end_time_text: str = ""
    end_result: str = ""
    raw_punch_count: int = 0
    valid_raw_punch_count: int = 0
    used_daily_fallback: bool = False
    punch_pair_complete: bool = False
    overtime_type: str = "工作日"
    lunch_break_minutes: int = 0
    approval_overtime_minutes: int | None = None
    approval_difference_minutes: int | None = None
    approval_review_reason: str = ""
    approved_leave_minutes: int = 0
    late_raw_minutes: int = 0
    late_leave_exempt_minutes: int = 0
    leave_review_reason: str = ""
    late_minutes: int = 0
    late_occurrence_number: int = 0
    late_grace_minutes: int = 0
    late_chargeable_minutes: int = 0
    late_status: str = ""
    # Leave minutes are kept in two forms: the raw wall-clock coverage from
    # the approval interval, and effective minutes after the fixed lunch
    # break is removed.  The latter is used for payroll classification.
    leave_work_minutes: int = 0
    leave_minutes_by_type: dict[str, int] = field(default_factory=dict)
    leave_non_deductible_minutes: int = 0
    leave_deductible_minutes: int = 0
    leave_overlap_minutes: int = 0
    leave_overlap_types: str = ""
    source_row: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def overtime_hours(self) -> float:
        return round(self.overtime_minutes / 60, 2)

    @property
    def has_overtime(self) -> bool:
        return self.overtime_minutes > 0
