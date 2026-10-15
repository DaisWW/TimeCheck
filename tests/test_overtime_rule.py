from datetime import date, datetime, timedelta
import unittest

from attendance_analyzer.analyzer import AttendanceAnalyzer
from attendance_analyzer.config import AppConfig, RestDayConfig, WorkdayConfig
from attendance_analyzer.excel_reader import fallback_punches
from attendance_analyzer.models import DailyAttendance, PunchRecord
from attendance_analyzer.rules import OvertimeRule


class OvertimeRuleTest(unittest.TestCase):
    def test_lateness_uses_fixed_lunch_even_when_flexible_end_lunch_differs(self):
        config = AppConfig(workday=WorkdayConfig(lunch_break_minutes=60))
        result = OvertimeRule(config).calculate(
            DailyAttendance("Alice", "u1", date(2026, 8, 3), "A 09:30-18:30", start_time_text="13:45", end_time_text="20:00"),
            [],
        )
        self.assertEqual(result.late_raw_minutes, 135)
        self.assertEqual(result.late_minutes, 135)

    def test_flexible_start_and_dinner_break(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance("Alice", "u1", date(2026, 5, 6), "A 09:30-18:30"),
            [
                PunchRecord("Alice", "u1", datetime(2026, 5, 6, 9, 30), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 5, 6, 20, 0), "正常", 2),
            ],
        )

        self.assertEqual(result.standard_end, datetime(2026, 5, 6, 18, 30))
        self.assertEqual(result.overtime_start, datetime(2026, 5, 6, 19, 0))
        self.assertEqual(result.overtime_minutes, 60)

    def test_overtime_is_not_capped_by_default(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance("Alice", "u1", date(2026, 5, 6), "A 09:30-18:30"),
            [
                PunchRecord("Alice", "u1", datetime(2026, 5, 6, 8, 50), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 5, 6, 22, 30), "正常", 2),
            ],
        )

        self.assertEqual(result.standard_end, datetime(2026, 5, 6, 18, 0))
        self.assertEqual(result.overtime_minutes, 240)

    def test_late_start_uses_fixed_19_00_end_without_shifting(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance("Alice", "u1", date(2026, 5, 6), "A 09:30-18:30"),
            [
                PunchRecord("Alice", "u1", datetime(2026, 5, 6, 10, 8), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 5, 7, 1, 24), "正常", 2),
            ],
        )

        self.assertEqual(result.standard_end, datetime(2026, 5, 6, 19, 0))
        self.assertEqual(result.overtime_start, datetime(2026, 5, 6, 19, 30))
        self.assertEqual(result.overtime_minutes, 354)
        self.assertEqual(result.late_minutes, 8)

    def test_partial_leave_reduces_late_minutes_and_marks_same_day_overtime_for_review(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance(
                "Alice",
                "u1",
                date(2026, 7, 1),
                "A 09:30-18:30",
                approval_text="事假07-01 10:00到07-01 12:00 2小时",
            ),
            [
                PunchRecord("Alice", "u1", datetime(2026, 7, 1, 12, 48), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 7, 1, 22, 0), "正常", 2),
            ],
        )

        self.assertEqual(result.approved_leave_minutes, 120)
        self.assertEqual(result.late_raw_minutes, 120)
        self.assertEqual(result.late_leave_exempt_minutes, 120)
        self.assertEqual(result.late_minutes, 0)
        self.assertEqual(result.overtime_minutes, 150)
        self.assertIn("请假与加班是否可同时计薪", result.leave_review_reason)

    def test_leave_minutes_are_split_by_type_and_fixed_lunch_is_removed(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance(
                "Alice",
                "u1",
                date(2026, 7, 1),
                "A 09:30-18:30",
                approval_text=(
                    "年假07-01 10:00到07-01 13:00 3小时，"
                    "事假07-01 13:30到07-01 15:00 1.5小时"
                ),
            ),
            [],
        )

        self.assertEqual(result.approved_leave_minutes, 270)
        self.assertEqual(result.leave_work_minutes, 210)
        self.assertEqual(result.leave_minutes_by_type, {"事假": 90, "年假": 120})
        self.assertEqual(result.leave_non_deductible_minutes, 120)
        self.assertEqual(result.leave_deductible_minutes, 90)

    def test_overlapping_leave_types_are_reported_for_hr_review(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance(
                "Alice",
                "u1",
                date(2026, 7, 1),
                "A 09:30-18:30",
                approval_text=(
                    "年假07-01 10:00到07-01 13:00 3小时，"
                    "事假07-01 11:00到07-01 12:00 1小时"
                ),
            ),
            [],
        )

        self.assertEqual(result.leave_overlap_minutes, 60)
        self.assertIn("年假/事假", result.leave_overlap_types)
        self.assertIn("请 HR 确认", result.leave_review_reason)

    def test_lunch_break_is_not_counted_as_late_after_leave(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance(
                "Alice",
                "u1",
                date(2026, 7, 1),
                "A 09:30-18:30",
                approval_text="事假07-01 10:00到07-01 12:00 2小时",
            ),
            [
                PunchRecord("Alice", "u1", datetime(2026, 7, 1, 13, 45), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 7, 1, 19, 0), "正常", 2),
            ],
        )

        self.assertEqual(result.late_raw_minutes, 135)
        self.assertEqual(result.late_leave_exempt_minutes, 120)
        self.assertEqual(result.late_minutes, 15)

    def test_multiday_leave_is_clipped_to_the_attendance_date(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance(
                "Alice",
                "u1",
                date(2026, 5, 9),
                "A 09:30-18:30",
                approval_text="年假05-08 09:30到05-22 18:30 12天",
            ),
            [],
        )

        self.assertEqual(result.approved_leave_minutes, 1440)
        self.assertEqual(result.leave_work_minutes, 450)
        self.assertEqual(result.leave_non_deductible_minutes, 450)

    def test_multiday_leave_excludes_rest_days(self):
        rule = OvertimeRule(AppConfig())
        results = []
        for offset in range(8):
            day = date(2026, 8, 15) + timedelta(days=offset)
            results.append(rule.calculate(DailyAttendance(
                "Alice", "u1", day, "休息" if day.weekday() >= 5 else "A 09:30-18:30",
                approval_text="年假08-15 08:05到08-22 08:05 37.5小时",
            ), []))
        self.assertEqual(sum(item.leave_work_minutes for item in results), 2250)
        self.assertTrue(all(item.leave_work_minutes == 0 for item in results if item.work_date.weekday() >= 5))

    def test_business_trip_is_attendance_and_sick_leave_wage_classification_is_pending(self):
        rule = OvertimeRule(AppConfig())
        for kind in ("外出", "出差"):
            with self.subTest(kind=kind):
                result = rule.calculate(DailyAttendance(
                    "Alice", "u1", date(2026, 8, 3), "A 09:30-18:30",
                    approval_text=f"{kind}08-03 09:00到08-03 12:00 2.5小时",
                ), [])
                self.assertEqual(result.business_attendance_minutes, 180)
                self.assertEqual(result.leave_work_minutes, 0)
                self.assertEqual(result.leave_deductible_minutes, 0)
        for kind in ("病假", "婚假", "请假"):
            with self.subTest(kind=kind):
                result = rule.calculate(DailyAttendance(
                    "Alice", "u1", date(2026, 8, 3), "A 09:30-18:30",
                    approval_text=f"{kind}08-03 09:00到08-03 12:00 3小时",
                ), [])
                self.assertEqual(result.leave_unclassified_minutes, 180)
                self.assertEqual(result.leave_non_deductible_minutes, 0)
                self.assertEqual(result.leave_deductible_minutes, 0)
                self.assertIn("工资分类未确认", result.leave_review_reason)

    def test_leave_uses_interval_minus_lunch_without_clipping_to_displayed_shift(self):
        result = OvertimeRule(AppConfig()).calculate(DailyAttendance(
            "Alice", "u1", date(2026, 8, 3), "A 09:30-18:30",
            approval_text="事假08-03 09:00到08-03 18:00 7小时",
        ), [])
        self.assertEqual(result.leave_work_minutes, 450)
        self.assertEqual(result.leave_deductible_minutes, 450)

    def test_additional_daily_punches_use_first_and_last_valid_punch(self):
        result = OvertimeRule(AppConfig()).calculate(DailyAttendance(
            "Alice", "u1", date(2026, 8, 15), "休息",
            start_time_text="10:43", end_time_text="18:07",
            additional_punches=(("18:19", "正常"), ("20:00", "打卡无效:已更新")),
            approval_text="加班08-15 10:43到08-15 18:07 7.4小时",
        ), [])
        self.assertEqual(result.overtime_minutes, 366)
        self.assertEqual(result.approval_overtime_minutes, 354)

    def test_duplicate_timestamp_is_not_a_punch_pair(self):
        punch = PunchRecord("Alice", "u1", datetime(2026, 8, 3, 10), "正常", 4)
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance("Alice", "u1", date(2026, 8, 3), "A 09:30-18:30"), [punch, punch]
        )
        self.assertFalse(result.punch_pair_complete)
        self.assertEqual(result.overtime_minutes, 0)

    def test_single_raw_punch_is_preserved_when_daily_fallback_is_empty(self):
        punch = PunchRecord("Alice", "u1", datetime(2026, 8, 3, 10, 15), "正常", 4)
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance("Alice", "u1", date(2026, 8, 3), "A 09:30-18:30"), [punch]
        )
        self.assertEqual(result.first_clock_in, punch.occurred_at)
        self.assertFalse(result.punch_pair_complete)

    def test_overlapping_overtime_approvals_are_counted_once(self):
        result = OvertimeRule(AppConfig()).calculate(DailyAttendance(
            "Alice", "u1", date(2026, 8, 1), "休息",
            start_time_text="09:00", end_time_text="18:00",
            approval_text="加班08-01 09:00到08-01 18:00，加班08-01 09:00到08-01 18:00",
        ), [])
        self.assertEqual(result.approval_overtime_minutes, 450)
        self.assertEqual(result.approval_difference_minutes, 0)

    def test_weekday_rest_shift_is_not_a_statutory_holiday(self):
        result = OvertimeRule(AppConfig()).calculate(DailyAttendance(
            "Alice", "u1", date(2026, 8, 3), "休息", start_time_text="10:00", end_time_text="18:00",
        ), [])
        self.assertEqual(result.overtime_type, "休息日")

    def test_late_leave_overlap_does_not_create_negative_exemption_before_lunch(self):
        result = OvertimeRule(AppConfig()).calculate(DailyAttendance(
            "Alice", "u1", date(2026, 8, 3), "A 09:30-18:30",
            start_time_text="10:15", end_time_text="18:30",
            approval_text="事假08-03 10:00到08-03 12:00 2小时",
        ), [])
        self.assertEqual(result.late_raw_minutes, 15)
        self.assertEqual(result.late_leave_exempt_minutes, 15)
        self.assertEqual(result.late_minutes, 0)

    def test_cross_midnight_clock_out_counts_for_the_attendance_date(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance("Alice", "u1", date(2026, 5, 6), "A 09:30-18:30"),
            [
                PunchRecord("Alice", "u1", datetime(2026, 5, 6, 10, 8), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 5, 7, 1, 24), "正常", 2),
            ],
        )

        self.assertEqual(result.standard_end, datetime(2026, 5, 6, 19, 0))
        self.assertEqual(result.overtime_start, datetime(2026, 5, 6, 19, 30))
        self.assertEqual(result.overtime_minutes, 354)

    def test_daily_fallback_interprets_next_day_clock_out(self):
        punches = fallback_punches(
            DailyAttendance(
                "Alice",
                "u1",
                date(2026, 5, 6),
                "A 09:30-18:30",
                start_time_text="10:08",
                end_time_text="次日 01:24",
            )
        )

        self.assertEqual(
            [punch.occurred_at for punch in punches],
            [datetime(2026, 5, 6, 10, 8), datetime(2026, 5, 7, 1, 24)],
        )

    def test_incomplete_punch_pair_is_not_calculation_ready(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance("Alice", "u1", date(2026, 5, 6), "A 09:30-18:30"),
            [PunchRecord("Alice", "u1", datetime(2026, 5, 6, 9, 30), "正常", 1)],
        )

        self.assertFalse(result.punch_pair_complete)
        self.assertEqual(result.overtime_minutes, 0)
        self.assertIn("有效上下班打卡不足", result.notes)

    def test_rest_day_deducts_fixed_lunch_but_not_dinner(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance(
                "Alice",
                "u1",
                date(2026, 7, 11),
                "休息",
                approval_text="加班07-11 09:45到07-11 19:00",
            ),
            [
                PunchRecord("Alice", "u1", datetime(2026, 7, 11, 9, 45), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 7, 11, 19, 0), "正常", 2),
            ],
        )

        self.assertEqual(result.overtime_type, "休息日")
        self.assertEqual(result.raw_overtime_minutes, 555)
        self.assertEqual(result.lunch_break_minutes, 90)
        self.assertEqual(result.dinner_break_minutes, 0)
        self.assertEqual(result.overtime_minutes, 465)
        self.assertEqual(result.approval_overtime_minutes, 465)
        self.assertEqual(result.approval_difference_minutes, 0)
        self.assertEqual(result.approval_review_reason, "")

    def test_approval_within_tolerance_uses_actual_rest_day_minutes(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance(
                "Alice",
                "u1",
                date(2026, 7, 18),
                "休息",
                approval_text="加班07-18 11:05到07-18 18:27",
            ),
            [
                PunchRecord("Alice", "u1", datetime(2026, 7, 18, 11, 5), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 7, 18, 18, 26), "正常", 2),
            ],
        )

        self.assertEqual(result.overtime_minutes, 351)
        self.assertEqual(result.approval_overtime_minutes, 352)
        self.assertEqual(result.approval_difference_minutes, 1)
        self.assertEqual(result.approval_review_reason, "")

    def test_approval_over_tolerance_requires_review_but_keeps_actual_minutes(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance(
                "Alice",
                "u1",
                date(2026, 7, 18),
                "休息",
                approval_text="加班07-18 09:00到07-18 18:00",
            ),
            [
                PunchRecord("Alice", "u1", datetime(2026, 7, 18, 11, 5), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 7, 18, 18, 26), "正常", 2),
            ],
        )

        self.assertEqual(result.overtime_minutes, 351)
        self.assertEqual(result.approval_overtime_minutes, 450)
        self.assertEqual(result.approval_difference_minutes, 99)
        self.assertIn("超过 30 分钟容差", result.approval_review_reason)

    def test_workday_approval_is_not_checked(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance(
                "Alice",
                "u1",
                date(2026, 7, 1),
                "A 09:30-18:30",
                approval_text="加班07-01 20:00到07-01 22:00",
            ),
            [
                PunchRecord("Alice", "u1", datetime(2026, 7, 1, 9, 49), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 7, 1, 22, 4), "正常", 2),
            ],
        )

        self.assertEqual(result.overtime_minutes, 165)
        self.assertIsNone(result.approval_overtime_minutes)
        self.assertIsNone(result.approval_difference_minutes)
        self.assertEqual(result.approval_review_reason, "")

    def test_rest_day_punches_without_overtime_approval_require_review(self):
        result = OvertimeRule(AppConfig()).calculate(
            DailyAttendance("Alice", "u1", date(2026, 7, 11), "休息"),
            [
                PunchRecord("Alice", "u1", datetime(2026, 7, 11, 14, 0), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 7, 11, 18, 0), "正常", 2),
            ],
        )

        self.assertEqual(result.overtime_minutes, 240)
        self.assertIn("未找到可解析的加班申请", result.approval_review_reason)

    def test_non_workday_dinner_is_not_deducted_by_default(self):
        config = AppConfig(
            rest_day=RestDayConfig(holiday_dates=(date(2026, 10, 1),)),
        )
        result = OvertimeRule(config).calculate(
            DailyAttendance("Alice", "u1", date(2026, 10, 1), "休息"),
            [
                PunchRecord("Alice", "u1", datetime(2026, 10, 1, 9, 45), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 10, 1, 20, 30), "正常", 2),
            ],
        )

        self.assertEqual(result.overtime_type, "节假日")
        self.assertEqual(result.lunch_break_minutes, 90)
        self.assertEqual(result.dinner_break_minutes, 0)
        self.assertEqual(result.overtime_minutes, 555)

    def test_explicit_holiday_date_overrides_workday_shift(self):
        config = AppConfig(
            rest_day=RestDayConfig(holiday_dates=(date(2026, 10, 1),)),
        )
        result = OvertimeRule(config).calculate(
            DailyAttendance("Alice", "u1", date(2026, 10, 1), "A 09:30-18:30"),
            [
                PunchRecord("Alice", "u1", datetime(2026, 10, 1, 9, 45), "正常", 1),
                PunchRecord("Alice", "u1", datetime(2026, 10, 1, 20, 30), "正常", 2),
            ],
        )

        self.assertEqual(result.overtime_type, "节假日")
        self.assertEqual(result.lunch_break_minutes, 90)
        self.assertEqual(result.dinner_break_minutes, 0)
        self.assertEqual(result.overtime_minutes, 555)

    def test_incomplete_holiday_record_uses_unified_non_workday_dinner_config(self):
        config = AppConfig(
            rest_day=RestDayConfig(holiday_dates=(date(2026, 10, 1),)),
        )
        result = OvertimeRule(config).calculate(
            DailyAttendance("Alice", "u1", date(2026, 10, 1), "休息"),
            [],
        )

        self.assertFalse(result.punch_pair_complete)
        self.assertEqual(result.overtime_type, "节假日")
        self.assertEqual(result.dinner_break_minutes, 0)

    def test_monthly_late_grace_handles_45_minute_late(self):
        rule = OvertimeRule(AppConfig())
        results = []
        for day, start in ((date(2026, 7, 1), datetime(2026, 7, 1, 10, 45)),
                           (date(2026, 7, 2), datetime(2026, 7, 2, 10, 10))):
            results.append(
                rule.calculate(
                    DailyAttendance("Alice", "u1", day, "A"),
                    [
                        PunchRecord("Alice", "u1", start, "正常", 1),
                        PunchRecord("Alice", "u1", datetime(day.year, day.month, day.day, 19, 0), "正常", 2),
                    ],
                )
            )

        allocated = AttendanceAnalyzer(AppConfig())._allocate_monthly_late_grace(results)

        self.assertEqual(allocated[0].late_minutes, 45)
        self.assertEqual(allocated[0].late_grace_minutes, 30)
        self.assertEqual(allocated[0].late_chargeable_minutes, 15)
        self.assertEqual(allocated[1].late_minutes, 10)
        self.assertEqual(allocated[1].late_grace_minutes, 0)
        self.assertEqual(allocated[1].late_chargeable_minutes, 10)

    def test_monthly_late_grace_handles_four_one_minute_lates(self):
        rule = OvertimeRule(AppConfig())
        results = []
        for day_number in range(1, 5):
            day = date(2026, 7, day_number)
            results.append(
                rule.calculate(
                    DailyAttendance("Alice", "u1", day, "A"),
                    [
                        PunchRecord("Alice", "u1", datetime(2026, 7, day_number, 10, 1), "正常", 1),
                        PunchRecord("Alice", "u1", datetime(2026, 7, day_number, 19, 0), "正常", 2),
                    ],
                )
            )

        allocated = AttendanceAnalyzer(AppConfig())._allocate_monthly_late_grace(results)

        self.assertEqual([item.late_chargeable_minutes for item in allocated], [0, 0, 0, 1])


if __name__ == "__main__":
    unittest.main()
