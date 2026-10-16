from datetime import date
import unittest

from attendance_analyzer.company_calendar import CompanyHoliday
from attendance_analyzer.config import AppConfig
from attendance_analyzer.config_forms import apply_rule_values, holiday_range_dates, rule_form_values, parse_date_text


HOLIDAY = CompanyHoliday(date(2026, 10, 19), "上午", "活动，保留备注")


class RuleFormTest(unittest.TestCase):
    def test_all_rule_types_round_trip_and_changes_preserve_company_holidays(self):
        config = AppConfig(company_holidays=(HOLIDAY,))
        values = rule_form_values(config)
        self.assertEqual(apply_rule_values(config, values), config)
        values.update({
            "workday.work_minutes": "420", "workday.count_non_workday_overtime": "否",
            "workday.flex_start_begin": "9:05", "overtime.rounding_mode": "向上取整",
            "rest_day.holiday_dates": "2026-10-01;2026/10/2", "overtime.invalid_punch_prefixes": "打卡无效；无效卡",
        })
        changed = apply_rule_values(config, values)
        self.assertEqual(changed.company_holidays, (HOLIDAY,))
        self.assertEqual(changed.workday.work_minutes, 420)
        self.assertFalse(changed.workday.count_non_workday_overtime)
        self.assertEqual(changed.overtime.rounding_mode, "ceil")
        self.assertEqual(changed.overtime.invalid_punch_prefixes, ("打卡无效", "无效卡"))
        self.assertEqual(changed.rest_day.holiday_dates, (date(2026, 10, 1), date(2026, 10, 2)))
        self.assertEqual(apply_rule_values(changed, rule_form_values(changed)), changed)

    def test_invalid_form_numbers_times_lists_and_dates_are_rejected(self):
        for key, value in (
            ("workday.work_minutes", "7.5"), ("workday.work_minutes", "450分钟"),
            ("workday.flex_start_begin", "25:00"), ("workday.rest_shift_keywords", "休息;;"),
            ("rest_day.holiday_dates", "2026-02-30"), ("rest_day.holiday_dates", "26-10-19"),
        ):
            with self.subTest(key=key, value=value):
                values = rule_form_values(AppConfig())
                values[key] = value
                with self.assertRaises(ValueError):
                    apply_rule_values(AppConfig(), values)


class HolidayRangeTest(unittest.TestCase):
    def test_ranges_include_endpoints_weekends_leap_days_and_cross_year(self):
        for start, end, expected in (
            ("2026-10-19", "2026-10-19", (date(2026, 10, 19),)),
            ("2026-10-30", "2026-11-02", (date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2))),
            ("2026-12-31", "2027-01-01", (date(2026, 12, 31), date(2027, 1, 1))),
            ("2028-02-28", "2028-03-01", (date(2028, 2, 28), date(2028, 2, 29), date(2028, 3, 1))),
            ("9999-12-31", "9999-12-31", (date.max,)),
        ):
            with self.subTest(start=start, end=end):
                self.assertEqual(holiday_range_dates(start, end), expected)
        self.assertEqual(len(holiday_range_dates("2028-01-01", "2028-12-31")), 366)

    def test_invalid_dates_reverse_and_excessive_ranges_are_rejected(self):
        for invalid in ("", "2026-02-29", "2026-2-01", "20261019", "2026-W43-1", "2026-10-19备注"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_date_text(invalid)
                with self.assertRaisesRegex(ValueError, "开始日期"):
                    holiday_range_dates(invalid, "2026-10-19")
                with self.assertRaisesRegex(ValueError, "结束日期"):
                    holiday_range_dates("2026-10-19", invalid)
        with self.assertRaisesRegex(ValueError, "不能早于"):
            holiday_range_dates("2026-10-20", "2026-10-19")
        with self.assertRaisesRegex(ValueError, "最多加入366天"):
            holiday_range_dates("2026-01-01", "2027-01-02")
