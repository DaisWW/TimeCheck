from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta

from .approval import (
    BUSINESS_ATTENDANCE_TYPES,
    LeaveApprovalInterval,
    OvertimeApprovalInterval,
    overlapping_interval_minutes,
    parse_leave_approval_intervals,
    parse_overtime_approval_intervals,
)
from .config import AppConfig
from .excel_reader import fallback_punches
from .models import DailyAttendance, OvertimeResult, PunchRecord
from .time_utils import minutes_between, parse_shift_times, round_minutes


NON_DEDUCTIBLE_LEAVE_TYPES = frozenset({"年假", "调休"})
DEDUCTIBLE_LEAVE_TYPES = frozenset({"事假"})


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
        if len({punch.occurred_at for punch in punches}) < 2:
            fallback = self._valid_punches(fallback_punches(day))
            if len({punch.occurred_at for punch in fallback}) >= 2 or (fallback and not punches):
                punches = fallback
                notes.append("使用每日统计打卡时间兜底")
                used_daily_fallback = True

        punch_pair_complete = len({punch.occurred_at for punch in punches}) >= 2
        first_clock_in = punches[0].occurred_at if punches else None
        start_known = bool(day.start_time_text.strip()) or punch_pair_complete
        leave_intervals = parse_leave_approval_intervals(day.approval_text, day.work_date.year)
        late_raw_minutes, late_leave_exempt_minutes, late_company_exempt_minutes, late_minutes = self._late_minutes(
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
            holiday = self._config.company_holiday_on(day.work_date)
            no_attendance = not (
                raw_punch_count or day.start_time_text or day.end_time_text
                or any(text for text, _ in day.additional_punches) or "加班" in day.approval_text
            )
            result = self._empty_result(
                day,
                "公司全天放假，无需考勤" if holiday and holiday.period == "全天" and no_attendance else "有效上下班打卡不足",
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
            late_company_exempt_minutes,
        )

    def _finalize_result(
        self,
        day: DailyAttendance,
        result: OvertimeResult,
        leave_intervals,
        late_raw_minutes: int,
        late_leave_exempt_minutes: int,
        late_company_exempt_minutes: int,
    ) -> OvertimeResult:
        holiday = self._config.company_holiday_on(day.work_date)
        work_start, work_end = self._work_bounds(day)
        required_minutes = self._config.workday.work_minutes if work_end > work_start else 0
        if holiday:
            if result.overtime_type == "工作日":
                required_minutes = min(minutes_between(work_start, work_end), self._config.workday.work_minutes)
            result = replace(
                result,
                company_holiday_period=holiday.period,
                company_holiday_note=holiday.note,
                notes=result.notes + [f"公司放假：{holiday.period}；{holiday.note}".rstrip("；")],
            )
            if result.overtime_type == "休息日" and holiday.period == "全天":
                result = replace(
                    result, company_holiday_overtime_minutes=result.overtime_minutes,
                    company_holiday_overtime_start=result.overtime_start,
                    company_holiday_overtime_end=result.overtime_end,
                )
            elif result.overtime_type == "休息日" and result.punch_pair_complete and result.last_clock_out:
                window = self._company_overtime_window(day, result.first_clock_in, result.last_clock_out)
                if window:
                    minutes = min(result.overtime_minutes, self._apply_minimum_and_round(self._approval_interval_minutes(*window, result.overtime_type)))
                    result = replace(
                        result, company_holiday_overtime_minutes=minutes,
                        company_holiday_overtime_start=window[0] if minutes else None,
                        company_holiday_overtime_end=window[1] if minutes else None,
                    )
        result = self._apply_approval_check(day, result)
        day_start = datetime.combine(day.work_date, time.min)
        day_end = day_start + timedelta(days=1)
        relevant_leave_intervals = [
            interval
            for interval in leave_intervals
            if interval.start < day_end and interval.end > day_start
        ]
        attendance_intervals = [i for i in relevant_leave_intervals if i.kind in BUSINESS_ATTENDANCE_TYPES]
        relevant_leave_intervals = [i for i in relevant_leave_intervals if i.kind not in BUSINESS_ATTENDANCE_TYPES]
        leave_breakdown = self._leave_breakdown(work_start, work_end, relevant_leave_intervals)
        attendance_minutes = self._effective_leave_minutes(work_start, work_end, attendance_intervals)
        leave_review_reasons: list[str] = []
        unclassified_types = {interval.kind for interval in relevant_leave_intervals} - NON_DEDUCTIBLE_LEAVE_TYPES - DEDUCTIBLE_LEAVE_TYPES
        if unclassified_types and leave_breakdown["work_minutes"]:
            leave_review_reasons.append(
                f"{'、'.join(sorted(unclassified_types))}的工资分类未确认，未归类 {leave_breakdown['unclassified_minutes']} 分钟；请 HR 确认。"
            )
        if leave_breakdown["overlap_minutes"]:
            leave_review_reasons.append(
                f"不同请假类型区间重叠 {leave_breakdown['overlap_minutes']} 分钟"
                f"（{leave_breakdown['overlap_types']}），已按已确认的工资类型优先暂列；"
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
            leave_unclassified_minutes=leave_breakdown["unclassified_minutes"],
            business_attendance_minutes=attendance_minutes,
            approved_non_punch_minutes=self._effective_leave_minutes(work_start, work_end, leave_intervals),
            additional_punch_count=sum(bool(text) for text, _ in day.additional_punches),
            leave_overlap_minutes=leave_breakdown["overlap_minutes"],
            leave_overlap_types=leave_breakdown["overlap_types"],
            late_raw_minutes=late_raw_minutes,
            late_leave_exempt_minutes=late_leave_exempt_minutes,
            late_company_holiday_exempt_minutes=late_company_exempt_minutes,
            required_work_minutes=required_minutes,
        )
        if result.leave_work_minutes and result.overtime_minutes:
            leave_types = "、".join(sorted({interval.kind for interval in relevant_leave_intervals}))
            leave_review_reasons.append(
                f"当天存在{leave_types}审批，同时有 {result.overtime_minutes} 分钟实际加班；"
                "请 HR 确认请假与加班是否可同时计薪。"
            )
        if leave_review_reasons:
            return replace(result, leave_review_reason="；".join(leave_review_reasons))
        return result

    def _leave_breakdown(self, day_start: datetime, day_end: datetime, intervals) -> dict:
        """Return the daily leave breakdown in effective working minutes.

        The approval interval is retained separately as a raw wall-clock union.
        For classification, a calendar day contributes at most
        ``workday.work_minutes`` after the fixed lunch interval is removed.  A
        disjoint segment representation keeps overlapping leave types accurate
        even when a long, multi-day approval is clipped to the daily limit.
        """
        segments = self._effective_leave_segments(day_start, day_end, intervals)
        segments = self._limit_leave_segments(segments, self._config.workday.work_minutes)

        minutes_by_type: dict[str, int] = {}
        non_paid_minutes = 0
        deductible_minutes = 0
        unclassified_minutes = 0
        overlap_minutes = 0
        overlap_types: set[str] = set()

        for start, end, kinds in segments:
            minutes = minutes_between(start, end)
            for kind in kinds:
                minutes_by_type[kind] = minutes_by_type.get(kind, 0) + minutes

            non_paid_kinds = kinds & NON_DEDUCTIBLE_LEAVE_TYPES
            paid_kinds = kinds & DEDUCTIBLE_LEAVE_TYPES
            if non_paid_kinds:
                # Non-deductible leave has priority for a minute covered by
                # both buckets, so the two classification buckets reconcile.
                non_paid_minutes += minutes
            elif paid_kinds:
                deductible_minutes += minutes
            else:
                unclassified_minutes += minutes
            if len(kinds) > 1:
                overlap_minutes += minutes
                overlap_types.add("/".join(sorted(kinds, key=lambda kind: (kind not in NON_DEDUCTIBLE_LEAVE_TYPES, kind))))

        return {
            "work_minutes": sum(minutes_between(start, end) for start, end, _ in segments),
            "minutes_by_type": dict(sorted(minutes_by_type.items())),
            "non_deductible_minutes": non_paid_minutes,
            "deductible_minutes": deductible_minutes,
            "unclassified_minutes": unclassified_minutes,
            "overlap_minutes": overlap_minutes,
            "overlap_types": "、".join(sorted(overlap_types)),
        }

    def _effective_leave_segments(self, day_start: datetime, day_end: datetime, intervals):
        """Build disjoint, lunch-excluded segments carrying their leave types."""
        clipped = [
            LeaveApprovalInterval(
                kind=interval.kind,
                start=max(interval.start, day_start),
                end=min(interval.end, day_end),
            )
            for interval in intervals
            if interval.start < day_end and interval.end > day_start
        ]
        clipped = [interval for interval in clipped if interval.end > interval.start]
        if not clipped:
            return []

        boundaries = {day_start, day_end}
        for interval in clipped:
            boundaries.add(interval.start)
            boundaries.add(interval.end)

        lunch_start = datetime.combine(day_start.date(), self._config.rest_day.fixed_lunch_start)
        lunch_end = lunch_start + timedelta(minutes=self._config.rest_day.fixed_lunch_minutes)
        if lunch_start < day_end and lunch_end > day_start:
            boundaries.add(max(lunch_start, day_start))
            boundaries.add(min(lunch_end, day_end))

        ordered = sorted(boundaries)
        segments = []
        for start, end in zip(ordered, ordered[1:]):
            if end <= start:
                continue
            if start < lunch_end and end > lunch_start:
                continue
            kinds = frozenset(
                interval.kind
                for interval in clipped
                if interval.start < end and interval.end > start
            )
            if kinds:
                segments.append((start, end, kinds))
        return segments

    @staticmethod
    def _limit_leave_segments(segments, maximum_minutes: int):
        """Keep the first ``maximum_minutes`` effective minutes of a day."""
        if maximum_minutes <= 0:
            return []
        remaining = maximum_minutes
        limited = []
        for start, end, kinds in segments:
            if remaining <= 0:
                break
            available = minutes_between(start, end)
            if available <= 0:
                continue
            take = min(available, remaining)
            limited.append((start, start + timedelta(minutes=take), kinds))
            remaining -= take
        return limited

    def _effective_leave_minutes(self, day_start: datetime, day_end: datetime, intervals) -> int:
        segments = self._effective_leave_segments(day_start, day_end, intervals)
        limited = self._limit_leave_segments(segments, self._config.workday.work_minutes)
        return sum(minutes_between(start, end) for start, end, _ in limited)

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
        holiday = self._config.company_holiday_on(day.work_date)
        if holiday and holiday.period == "上午":
            standard_end = datetime.combine(day.work_date, parse_shift_times(day.shift)[1])
        elif holiday and holiday.period == "下午":
            standard_end = datetime.combine(day.work_date, self._config.rest_day.fixed_lunch_start)
        else:
            standard_end = self._standard_end(first_clock_in)
        overtime_start = standard_end + timedelta(minutes=self._config.overtime.dinner_break_minutes)
        if holiday and holiday.period == "下午":
            overtime_start = standard_end + timedelta(minutes=self._config.rest_day.fixed_lunch_minutes)
        overtime_end = last_clock_out

        raw_minutes = 0 if holiday and holiday.period == "下午" else minutes_between(overtime_start, overtime_end)
        overtime_minutes = self._apply_minimum_and_round(raw_minutes)

        if overtime_minutes == 0 and last_clock_out > standard_end and not (holiday and holiday.period == "下午"):
            notes.append("未超过晚餐扣除时间")

        result = OvertimeResult(
            employee_name=day.employee_name,
            user_id=day.user_id,
            work_date=day.work_date,
            shift=day.shift,
            first_clock_in=first_clock_in,
            last_clock_out=last_clock_out,
            standard_end=standard_end,
            dinner_break_minutes=0 if holiday and holiday.period == "下午" else self._config.overtime.dinner_break_minutes,
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
            source_row=day.source_row,
            notes=notes,
        )
        return self._add_company_overtime(day, result)

    def _add_company_overtime(self, day: DailyAttendance, result: OvertimeResult) -> OvertimeResult:
        holiday = self._config.company_holiday_on(day.work_date)
        if not holiday or holiday.period == "全天":
            return result
        if not self._config.workday.count_non_workday_overtime:
            return replace(result, notes=result.notes + ["公司放假时段按配置不统计休息日加班"])
        window = self._company_overtime_window(day, result.first_clock_in, result.last_clock_out)
        if window is None:
            return result
        start, end = window
        lunch = self._fixed_break_overlap(start, end, self._config.rest_day.fixed_lunch_start, self._config.rest_day.fixed_lunch_minutes)
        dinner = self._fixed_break_overlap(start, end, self._config.rest_day.dinner_break_start, self._config.rest_day.dinner_break_minutes)
        raw_minutes = minutes_between(start, end)
        company_minutes = self._apply_minimum_and_round(max(raw_minutes - lunch - dinner, 0))
        lunch_start = datetime.combine(day.work_date, self._config.rest_day.fixed_lunch_start)
        lunch_end = lunch_start + timedelta(minutes=self._config.rest_day.fixed_lunch_minutes)
        effective_start = lunch_end if lunch_start <= start < lunch_end else start
        starts = ([result.overtime_start] if result.overtime_minutes else []) + ([effective_start] if company_minutes else [])
        ends = ([result.overtime_end] if result.overtime_minutes else []) + ([end] if company_minutes else [])
        return replace(
            result,
            overtime_minutes=result.overtime_minutes + company_minutes,
            raw_overtime_minutes=result.raw_overtime_minutes + raw_minutes,
            overtime_start=min(starts) if starts else result.overtime_start,
            overtime_end=max(ends) if ends else None,
            company_holiday_overtime_minutes=company_minutes,
            company_holiday_overtime_start=effective_start if company_minutes else None,
            company_holiday_overtime_end=end if company_minutes else None,
            lunch_break_minutes=lunch,
            dinner_break_minutes=dinner if holiday.period == "下午" else result.dinner_break_minutes,
            notes=result.notes + [
                f"公司{holiday.period}放假：放假时段实际工作按休息日加班计 {company_minutes} 分钟，"
                f"工作日加班另计 {result.overtime_minutes} 分钟；分段计算，不重复累计"
            ],
        )

    def _company_overtime_window(self, day: DailyAttendance, start: datetime, end: datetime):
        holiday = self._config.company_holiday_on(day.work_date)
        lunch_start = datetime.combine(day.work_date, self._config.rest_day.fixed_lunch_start)
        if holiday.period == "上午":
            start, end = max(start, datetime.combine(day.work_date, time.min)), min(end, lunch_start)
        else:
            start = max(start, lunch_start)
        return (start, end) if end > start else None

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
            source_row=day.source_row,
            notes=notes,
        )

    def _apply_approval_check(self, day: DailyAttendance, result: OvertimeResult) -> OvertimeResult:
        company_only = result.overtime_type == "工作日" and bool(result.company_holiday_period)
        if not result.punch_pair_complete or (result.overtime_type == "工作日" and not result.company_holiday_overtime_minutes):
            return result
        if result.overtime_type != "工作日" and not self._config.workday.count_non_workday_overtime:
            return result

        day_start = datetime.combine(day.work_date, time.min)
        day_end = day_start + timedelta(days=1)
        intervals = [
            interval for interval in parse_overtime_approval_intervals(day.approval_text, day.work_date.year)
            if interval.start < day_end and interval.end > day_start
        ]
        if not intervals:
            reason = "公司放假时段有实际加班" if company_only else "非工作日有完整打卡"
            return replace(result, approval_review_reason=f"{reason}，但未找到可解析的加班申请。")

        if any(interval.start < day_start or interval.end > day_end + timedelta(days=1) for interval in intervals):
            return replace(result, approval_review_reason="跨多日加班审批需要 HR 确认逐日分配，不直接把整段审批计入一天。")
        merged: list[OvertimeApprovalInterval] = []
        for interval in sorted(intervals, key=lambda item: item.start):
            if merged and interval.start <= merged[-1].end:
                merged[-1] = OvertimeApprovalInterval(merged[-1].start, max(merged[-1].end, interval.end))
            else:
                merged.append(interval)
        windows = [(interval.start, interval.end) for interval in merged]
        if company_only:
            windows = [window for start, end in windows if (window := self._company_overtime_window(day, start, end)) is not None]
        approval_minutes = sum(self._approval_interval_minutes(start, end, result.overtime_type) for start, end in windows)
        actual_minutes = result.company_holiday_overtime_minutes if company_only else result.overtime_minutes
        difference = abs(actual_minutes - approval_minutes)
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
                f"{'公司放假时段' if company_only else ''}实际加班 {actual_minutes} 分钟，与审批 {approval_minutes} 分钟相差 "
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
    ) -> tuple[int, int, int, int]:
        if (
            overtime_type != "工作日"
            or first_clock_in is None
            or not start_known
            or first_clock_in.date() != day.work_date
        ):
            return 0, 0, 0, 0

        latest_allowed_start = datetime.combine(day.work_date, self._config.workday.flex_start_end)
        work_start, work_end = self._work_bounds(day)
        holiday = self._config.company_holiday_on(day.work_date)
        comparison_end = min(first_clock_in, work_end) if holiday else first_clock_in
        lunch_start = datetime.combine(day.work_date, self._config.rest_day.fixed_lunch_start)
        lunch_end = lunch_start + timedelta(minutes=self._config.rest_day.fixed_lunch_minutes)
        lunch_overlap_minutes = minutes_between(
            max(latest_allowed_start, lunch_start),
            min(comparison_end, lunch_end),
        )
        raw_minutes = max(
            minutes_between(latest_allowed_start, comparison_end) - lunch_overlap_minutes,
            0,
        )
        company_exempt_minutes = 0
        if holiday and holiday.period == "上午":
            company_exempt_minutes = max(
                minutes_between(latest_allowed_start, min(comparison_end, work_start))
                - minutes_between(max(latest_allowed_start, lunch_start), min(comparison_end, work_start, lunch_end)),
                0,
            )
        approval_start = max(latest_allowed_start, work_start)
        approval_end = min(comparison_end, work_end)
        leave_exempt_minutes = overlapping_interval_minutes(
            approval_start,
            approval_end,
            leave_intervals,
        )
        leave_exempt_minutes -= overlapping_interval_minutes(
            max(approval_start, lunch_start),
            min(approval_end, lunch_end),
            leave_intervals,
        )
        return raw_minutes, leave_exempt_minutes, company_exempt_minutes, max(raw_minutes - company_exempt_minutes - leave_exempt_minutes, 0)

    def _work_bounds(self, day: DailyAttendance) -> tuple[datetime, datetime]:
        start = datetime.combine(day.work_date, time.min)
        if self._overtime_type(day) != "工作日":
            return start, start
        holiday = self._config.company_holiday_on(day.work_date)
        if not holiday:
            return start, start + timedelta(days=1)
        lunch_start = datetime.combine(day.work_date, self._config.rest_day.fixed_lunch_start)
        if holiday.period == "上午":
            try:
                shift_end = parse_shift_times(day.shift)[1]
            except ValueError as error:
                raise ValueError(f"每日统计 第 {day.source_row} 行 [班次] 原值 {day.shift!r}：公司上午放假需要明确当天原班次的下班时间") from error
            start, end = lunch_start + timedelta(minutes=self._config.rest_day.fixed_lunch_minutes), datetime.combine(day.work_date, shift_end)
        else:
            start, end = datetime.combine(day.work_date, self._config.workday.flex_start_begin), lunch_start
        if end <= start:
            raise ValueError(f"每日统计 第 {day.source_row} 行 [班次] 原值 {day.shift!r}：公司半天放假后没有可用的工作时段")
        return start, end

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
        return day.work_date in self._config.rest_day.holiday_dates

    def _overtime_type(self, day: DailyAttendance) -> str:
        if self._is_holiday(day):
            return "节假日"
        holiday = self._config.company_holiday_on(day.work_date)
        if holiday and holiday.period == "全天":
            return "休息日"
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
            source_row=day.source_row,
            notes=[note],
        )
