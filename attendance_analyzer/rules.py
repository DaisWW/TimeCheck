from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta

from .approval import (
    interval_union_minutes,
    overlapping_interval_minutes,
    parse_leave_approval_intervals,
    parse_overtime_approval_intervals,
)
from .config import AppConfig
from .excel_reader import fallback_punches
from .models import DailyAttendance, OvertimeResult, PunchRecord
from .time_utils import minutes_between, round_minutes


# These are the only approval types that do not create a wage deduction.
# Unknown or newly introduced leave types remain in the deductible bucket so
# they cannot silently disappear from the payroll review.
NON_DEDUCTIBLE_LEAVE_TYPES = frozenset({"年假", "调休"})


class OvertimeRule:
    def __init__(self, config: AppConfig):
        self._config = config

    def calculate(self, day: DailyAttendance, raw_punches: list[PunchRecord]) -> OvertimeResult:
        notes: list[str] = []
        overtime_type = self._overtime_type(day)
        raw_punch_count = len(raw_punches)
        valid_raw_punches = self._valid_punches(raw_punches)
        valid_raw_punch_count = len(valid_raw_punches)
        punches = valid_raw_punches
        used_daily_fallback = False
        if len(punches) < 2:
            punches = self._valid_punches(fallback_punches(day))
            if punches:
                notes.append("使用每日统计打卡时间兜底")
                used_daily_fallback = True

        punch_pair_complete = len(punches) >= 2
        first_clock_in = punches[0].occurred_at if punches else None
        start_known = bool(day.start_time_text.strip()) or punch_pair_complete
        leave_intervals = parse_leave_approval_intervals(day.approval_text, day.work_date.year)
        late_raw_minutes, late_leave_exempt_minutes, late_minutes = self._late_minutes(
            day,
            overtime_type,
            first_clock_in,
            start_known,
            leave_intervals,
        )

        if overtime_type != "工作日" and not self._config.workday.count_non_workday_overtime:
            result = self._empty_result(
                day,
                "非工作日暂不统计加班",
                raw_punch_count,
                valid_raw_punch_count,
                used_daily_fallback,
                punch_pair_complete,
                first_clock_in,
                late_minutes,
            )
        elif not punch_pair_complete:
            result = self._empty_result(
                day,
                "有效上下班打卡不足",
                raw_punch_count,
                valid_raw_punch_count,
                used_daily_fallback,
                punch_pair_complete,
                first_clock_in,
                late_minutes,
            )
        elif overtime_type != "工作日":
            result = self._calculate_rest_day(
                day,
                punches,
                raw_punch_count,
                valid_raw_punch_count,
                used_daily_fallback,
                notes,
                late_minutes,
            )
        else:
            result = self._calculate_workday(
                day,
                punches,
                raw_punch_count,
                valid_raw_punch_count,
                used_daily_fallback,
                notes,
                late_minutes,
            )

        return self._finalize_result(
            day,
            result,
            leave_intervals,
            late_raw_minutes,
            late_leave_exempt_minutes,
        )

    def _finalize_result(
        self,
        day: DailyAttendance,
        result: OvertimeResult,
        leave_intervals,
        late_raw_minutes: int,
        late_leave_exempt_minutes: int,
    ) -> OvertimeResult:
        result = self._apply_approval_check(day, result)
        day_start = datetime.combine(day.work_date, time.min)
        day_end = day_start + timedelta(days=1)
        relevant_leave_intervals = [
            interval
            for interval in leave_intervals
            if interval.start < day_end and interval.end > day_start
        ]
        leave_breakdown = self._leave_breakdown(day_start, day_end, relevant_leave_intervals)
        leave_review_reasons: list[str] = []
        if leave_breakdown["overlap_minutes"]:
            leave_review_reasons.append(
                f"不同请假类型区间重叠 {leave_breakdown['overlap_minutes']} 分钟"
                f"（{leave_breakdown['overlap_types']}），已按不扣工资类型优先归类；"
                "请 HR 确认。"
            )
        result = replace(
            result,
            approved_leave_minutes=overlapping_interval_minutes(
                day_start,
                day_end,
                relevant_leave_intervals,
            ),
            leave_work_minutes=leave_breakdown["work_minutes"],
            leave_minutes_by_type=leave_breakdown["minutes_by_type"],
            leave_non_deductible_minutes=leave_breakdown["non_deductible_minutes"],
            leave_deductible_minutes=leave_breakdown["deductible_minutes"],
            leave_overlap_minutes=leave_breakdown["overlap_minutes"],
            leave_overlap_types=leave_breakdown["overlap_types"],
            late_raw_minutes=late_raw_minutes,
            late_leave_exempt_minutes=late_leave_exempt_minutes,
        )
        if relevant_leave_intervals and result.overtime_minutes:
            leave_types = "、".join(sorted({interval.kind for interval in relevant_leave_intervals}))
            leave_review_reasons.append(
                f"当天存在{leave_types}审批，同时有 {result.overtime_minutes} 分钟实际加班；"
                "请 HR 确认请假与加班是否可同时计薪。"
            )
        if leave_review_reasons:
            return replace(result, leave_review_reason="；".join(leave_review_reasons))
        return result

    def _leave_breakdown(self, day_start: datetime, day_end: datetime, intervals) -> dict:
        """Return raw/effective leave minutes without double-counting overlaps.

        ``approved_leave_minutes`` remains the raw wall-clock union for
        backwards compatibility.  Effective leave minutes remove the fixed
        lunch interval.  When paid and non-paid types overlap, the overlap is
        reported and assigned to the non-paid bucket so the two payroll
        buckets still reconcile to the effective total.
        """
        by_kind: dict[str, list] = {}
        for interval in intervals:
            by_kind.setdefault(interval.kind, []).append(interval)

        minutes_by_type = {
            kind: self._effective_leave_minutes(day_start, day_end, kind_intervals)
            for kind, kind_intervals in sorted(by_kind.items())
        }
        effective_total = self._effective_leave_minutes(day_start, day_end, intervals)
        non_paid_intervals = [
            interval for interval in intervals if interval.kind in NON_DEDUCTIBLE_LEAVE_TYPES
        ]
        paid_intervals = [
            interval for interval in intervals if interval.kind not in NON_DEDUCTIBLE_LEAVE_TYPES
        ]
        non_paid_minutes = self._effective_leave_minutes(day_start, day_end, non_paid_intervals)
        paid_minutes = self._effective_leave_minutes(day_start, day_end, paid_intervals)
        overlap_minutes = max(non_paid_minutes + paid_minutes - effective_total, 0)
        overlap_types: set[str] = set()
        for left_index, left in enumerate(non_paid_intervals):
            for right in paid_intervals:
                overlap_start = max(left.start, right.start, day_start)
                overlap_end = min(left.end, right.end, day_end)
                if self._effective_leave_minutes(
                    day_start, day_end, [left.__class__(**{**left.__dict__, "start": overlap_start, "end": overlap_end})]
                ):
                    overlap_types.add(f"{left.kind}/{right.kind}")

        return {
            "work_minutes": effective_total,
            "minutes_by_type": minutes_by_type,
            "non_deductible_minutes": non_paid_minutes,
            "deductible_minutes": max(paid_minutes - overlap_minutes, 0),
            "overlap_minutes": overlap_minutes,
            "overlap_types": "、".join(sorted(overlap_types)),
        }

    def _effective_leave_minutes(self, day_start: datetime, day_end: datetime, intervals) -> int:
        if not intervals:
            return 0
        clipped = [
            interval.__class__(
                **{
                    **interval.__dict__,
                    "start": max(interval.start, day_start),
                    "end": min(interval.end, day_end),
                }
            )
            for interval in intervals
            if interval.start < day_end and interval.end > day_start
        ]
        clipped = [interval for interval in clipped if interval.end > interval.start]
        if not clipped:
            return 0
        raw_minutes = interval_union_minutes(clipped)
        lunch_overlap = self._break_overlap_for_intervals(
            clipped,
            day_start,
            day_end,
            self._config.rest_day.fixed_lunch_start,
            self._config.rest_day.fixed_lunch_minutes,
        )
        return max(raw_minutes - lunch_overlap, 0)

    def _break_overlap_for_intervals(
        self,
        intervals,
        day_start: datetime,
        day_end: datetime,
        break_start,
        break_minutes: int,
    ) -> int:
        if not intervals or break_minutes <= 0:
            return 0
        total = 0
        current_date = day_start.date()
        last_date = day_end.date()
        while current_date <= last_date:
            period_start = datetime.combine(current_date, break_start)
            period_end = period_start + timedelta(minutes=break_minutes)
            total += overlapping_interval_minutes(period_start, period_end, intervals)
            current_date += timedelta(days=1)
        return total

    def _calculate_workday(
        self,
        day: DailyAttendance,
        punches: list[PunchRecord],
        raw_punch_count: int,
        valid_raw_punch_count: int,
        used_daily_fallback: bool,
        notes: list[str],
        late_minutes: int,
    ) -> OvertimeResult:
        first_clock_in = punches[0].occurred_at
        last_clock_out = punches[-1].occurred_at
        standard_end = self._standard_end(first_clock_in)
        overtime_start = standard_end + timedelta(minutes=self._config.overtime.dinner_break_minutes)
        overtime_end = last_clock_out

        raw_minutes = minutes_between(overtime_start, overtime_end)
        overtime_minutes = self._apply_minimum_and_round(raw_minutes)

        if overtime_minutes == 0 and last_clock_out > standard_end:
            notes.append("未超过晚餐扣除时间")

        return OvertimeResult(
            employee_name=day.employee_name,
            user_id=day.user_id,
            work_date=day.work_date,
            shift=day.shift,
            first_clock_in=first_clock_in,
            last_clock_out=last_clock_out,
            standard_end=standard_end,
            dinner_break_minutes=self._config.overtime.dinner_break_minutes,
            overtime_start=overtime_start,
            overtime_end=overtime_end if overtime_minutes else None,
            raw_overtime_minutes=raw_minutes,
            overtime_minutes=overtime_minutes,
            approval_text=day.approval_text,
            start_time_text=day.start_time_text,
            start_result=day.start_result,
            end_time_text=day.end_time_text,
            end_result=day.end_result,
            raw_punch_count=raw_punch_count,
            valid_raw_punch_count=valid_raw_punch_count,
            used_daily_fallback=used_daily_fallback,
            punch_pair_complete=True,
            overtime_type="工作日",
            late_minutes=late_minutes,
            notes=notes,
        )

    def _calculate_rest_day(
        self,
        day: DailyAttendance,
        punches: list[PunchRecord],
        raw_punch_count: int,
        valid_raw_punch_count: int,
        used_daily_fallback: bool,
        notes: list[str],
        late_minutes: int,
    ) -> OvertimeResult:
        first_clock_in = punches[0].occurred_at
        last_clock_out = punches[-1].occurred_at
        overtime_type = self._overtime_type(day)
        lunch_break_minutes = self._fixed_break_overlap(
            first_clock_in,
            last_clock_out,
            self._config.rest_day.fixed_lunch_start,
            self._config.rest_day.fixed_lunch_minutes,
        )
        dinner_break_minutes = self._fixed_break_overlap(
            first_clock_in,
            last_clock_out,
            self._config.rest_day.dinner_break_start,
            self._config.rest_day.dinner_break_minutes,
        )

        raw_minutes = minutes_between(first_clock_in, last_clock_out)
        overtime_minutes = self._apply_minimum_and_round(
            raw_minutes - lunch_break_minutes - dinner_break_minutes
        )
        notes.append(f"{overtime_type}按实际首末有效打卡计算，固定午休已扣除")

        return OvertimeResult(
            employee_name=day.employee_name,
            user_id=day.user_id,
            work_date=day.work_date,
            shift=day.shift,
            first_clock_in=first_clock_in,
            last_clock_out=last_clock_out,
            standard_end=None,
            dinner_break_minutes=dinner_break_minutes,
            overtime_start=first_clock_in,
            overtime_end=last_clock_out if overtime_minutes else None,
            raw_overtime_minutes=raw_minutes,
            overtime_minutes=overtime_minutes,
            approval_text=day.approval_text,
            start_time_text=day.start_time_text,
            start_result=day.start_result,
            end_time_text=day.end_time_text,
            end_result=day.end_result,
            raw_punch_count=raw_punch_count,
            valid_raw_punch_count=valid_raw_punch_count,
            used_daily_fallback=used_daily_fallback,
            punch_pair_complete=True,
            overtime_type=overtime_type,
            lunch_break_minutes=lunch_break_minutes,
            late_minutes=late_minutes,
            notes=notes,
        )

    def _apply_approval_check(self, day: DailyAttendance, result: OvertimeResult) -> OvertimeResult:
        if not result.punch_pair_complete or result.overtime_type == "工作日":
            return result

        intervals = parse_overtime_approval_intervals(day.approval_text, day.work_date.year)
        if not intervals:
            return replace(result, approval_review_reason="非工作日有完整打卡，但未找到可解析的加班申请。")

        approval_minutes = sum(
            self._approval_interval_minutes(interval.start, interval.end, result.overtime_type)
            for interval in intervals
        )
        difference = abs(result.overtime_minutes - approval_minutes)
        if difference <= self._config.rest_day.approval_difference_tolerance_minutes:
            return replace(
                result,
                approval_overtime_minutes=approval_minutes,
                approval_difference_minutes=difference,
            )

        return replace(
            result,
            approval_overtime_minutes=approval_minutes,
            approval_difference_minutes=difference,
            approval_review_reason=(
                f"实际加班 {result.overtime_minutes} 分钟，与审批 {approval_minutes} 分钟相差 "
                f"{difference} 分钟，超过 {self._config.rest_day.approval_difference_tolerance_minutes} 分钟容差。"
            ),
        )

    def _approval_interval_minutes(self, start: datetime, end: datetime, overtime_type: str) -> int:
        minutes = minutes_between(start, end)
        minutes -= self._fixed_break_overlap(
            start,
            end,
            self._config.rest_day.fixed_lunch_start,
            self._config.rest_day.fixed_lunch_minutes,
        )
        minutes -= self._fixed_break_overlap(
            start,
            end,
            self._config.rest_day.dinner_break_start,
            self._config.rest_day.dinner_break_minutes,
        )
        return max(minutes, 0)

    def _fixed_break_overlap(
        self,
        start: datetime,
        end: datetime,
        break_start,
        break_minutes: int,
    ) -> int:
        if break_minutes == 0 or end <= start:
            return 0

        total = 0
        current_date = start.date()
        while current_date <= end.date():
            period_start = datetime.combine(current_date, break_start)
            period_end = period_start + timedelta(minutes=break_minutes)
            total += minutes_between(max(start, period_start), min(end, period_end))
            current_date += timedelta(days=1)
        return total

    def _apply_minimum_and_round(self, minutes: int) -> int:
        if minutes < self._config.overtime.min_overtime_minutes:
            return 0
        return round_minutes(
            minutes,
            self._config.overtime.rounding_unit_minutes,
            self._config.overtime.rounding_mode,
        )

    def _standard_end(self, first_clock_in: datetime) -> datetime:
        workday = self._config.workday
        begin = datetime.combine(first_clock_in.date(), workday.flex_start_begin)
        end = datetime.combine(first_clock_in.date(), workday.flex_start_end)

        # 弹性下班 = 有效上班起算点 + 工作时长 + 午休时长。
        if first_clock_in < begin:
            start_ref = begin
        elif first_clock_in <= end:
            start_ref = first_clock_in
        else:
            start_ref = end

        return start_ref + timedelta(minutes=workday.work_minutes + workday.lunch_break_minutes)

    def _late_minutes(
        self,
        day: DailyAttendance,
        overtime_type: str,
        first_clock_in: datetime | None,
        start_known: bool,
        leave_intervals,
    ) -> tuple[int, int, int]:
        if (
            overtime_type != "工作日"
            or first_clock_in is None
            or not start_known
            or first_clock_in.date() != day.work_date
        ):
            return 0, 0, 0

        latest_allowed_start = datetime.combine(day.work_date, self._config.workday.flex_start_end)
        lunch_start = datetime.combine(day.work_date, self._config.rest_day.fixed_lunch_start)
        lunch_end = lunch_start + timedelta(minutes=self._config.workday.lunch_break_minutes)
        lunch_overlap_minutes = minutes_between(
            max(latest_allowed_start, lunch_start),
            min(first_clock_in, lunch_end),
        )
        raw_minutes = max(
            minutes_between(latest_allowed_start, first_clock_in) - lunch_overlap_minutes,
            0,
        )
        leave_exempt_minutes = overlapping_interval_minutes(
            latest_allowed_start,
            first_clock_in,
            leave_intervals,
        )
        leave_exempt_minutes -= overlapping_interval_minutes(
            max(latest_allowed_start, lunch_start),
            min(first_clock_in, lunch_end),
            leave_intervals,
        )
        return raw_minutes, leave_exempt_minutes, max(raw_minutes - leave_exempt_minutes, 0)

    def _valid_punches(self, punches: list[PunchRecord]) -> list[PunchRecord]:
        invalid_prefixes = self._config.overtime.invalid_punch_prefixes
        valid = []
        for punch in punches:
            result = punch.result.strip()
            if any(result.startswith(prefix) for prefix in invalid_prefixes):
                continue
            valid.append(punch)
        return sorted(valid, key=lambda punch: punch.occurred_at)

    def _is_rest_day(self, day: DailyAttendance) -> bool:
        shift = day.shift.strip()
        return any(keyword in shift for keyword in self._config.workday.rest_shift_keywords)

    def _is_holiday(self, day: DailyAttendance) -> bool:
        return day.work_date in self._config.rest_day.holiday_dates or (
            self._is_rest_day(day) and day.work_date.weekday() < 5
        )

    def _overtime_type(self, day: DailyAttendance) -> str:
        if self._is_holiday(day):
            return "节假日"
        return "休息日" if self._is_rest_day(day) else "工作日"

    def _empty_result(
        self,
        day: DailyAttendance,
        note: str,
        raw_punch_count: int = 0,
        valid_raw_punch_count: int = 0,
        used_daily_fallback: bool = False,
        punch_pair_complete: bool = False,
        first_clock_in: datetime | None = None,
        late_minutes: int = 0,
    ) -> OvertimeResult:
        overtime_type = self._overtime_type(day)
        dinner_break_minutes = self._config.overtime.dinner_break_minutes
        if overtime_type != "工作日":
            dinner_break_minutes = self._config.rest_day.dinner_break_minutes

        return OvertimeResult(
            employee_name=day.employee_name,
            user_id=day.user_id,
            work_date=day.work_date,
            shift=day.shift,
            first_clock_in=first_clock_in,
            last_clock_out=None,
            standard_end=None,
            dinner_break_minutes=dinner_break_minutes,
            overtime_start=None,
            overtime_end=None,
            raw_overtime_minutes=0,
            overtime_minutes=0,
            approval_text=day.approval_text,
            start_time_text=day.start_time_text,
            start_result=day.start_result,
            end_time_text=day.end_time_text,
            end_result=day.end_result,
            raw_punch_count=raw_punch_count,
            valid_raw_punch_count=valid_raw_punch_count,
            used_daily_fallback=used_daily_fallback,
            punch_pair_complete=punch_pair_complete,
            overtime_type=overtime_type,
            late_minutes=late_minutes,
            notes=[note],
        )
