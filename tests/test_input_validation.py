from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from attendance_analyzer.config import AppConfig
from attendance_analyzer.excel_reader import DingTalkWorkbookReader, WorkbookInputError
from attendance_analyzer.time_utils import combine_date_time, parse_date_text, parse_datetime, parse_time


def valid_workbook():
    workbook = Workbook()
    daily = workbook.active
    daily.title = "每日统计"
    daily.append([])
    daily.append([])
    daily.append([
        "姓名", "UserId", "日期", "班次", "上班1打卡时间", "上班1打卡结果",
        "下班1打卡时间", "下班1打卡结果", "关联的审批单",
    ])
    daily.append([])
    daily.append(["Alice", "u1", "26-08-03 星期一", "A 09:30-18:30", "09:30", "正常", "20:00", "正常", ""])
    raw = workbook.create_sheet("原始记录")
    raw.append([])
    raw.append([])
    raw.append(["姓名", "UserId", "考勤日期", "打卡时间", "打卡结果"])
    raw.append(["Alice", "u1", "26-08-03 星期一", "2026-08-03 09:30", "正常"])
    raw.append(["Alice", "u1", "26-08-03 星期一", "2026-08-03 20:00", "正常"])
    return workbook


class InputValidationTest(unittest.TestCase):
    def read(self, workbook):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attendance.xlsx"
            workbook.save(path)
            try:
                return DingTalkWorkbookReader(AppConfig()).read(path)
            finally:
                workbook.close()

    def test_valid_export_and_trailing_blank_rows_are_accepted(self):
        workbook = valid_workbook()
        workbook["每日统计"].cell(7, 1).number_format = "@"
        workbook["原始记录"].cell(8, 1).number_format = "@"
        data = self.read(workbook)
        self.assertEqual(len(data.daily_records), 1)
        self.assertEqual(len(data.raw_punches[("u1", date(2026, 8, 3))]), 2)
        self.assertEqual(data.input_issues, [])

    def test_all_bad_cells_are_reported_with_source_and_original_value(self):
        workbook = valid_workbook()
        workbook["每日统计"]["E5"] = "10:30 trailing"
        workbook["每日统计"]["I5"] = "产假08-03 09:00到08-03 18:00"
        workbook["原始记录"]["D4"] = "2026-08-03 25:00"
        with self.assertRaises(WorkbookInputError) as raised:
            self.read(workbook)
        error = str(raised.exception)
        for fragment in ("每日统计 第 5 行", "原始记录 第 4 行", "10:30 trailing", "产假", "25:00"):
            self.assertIn(fragment, error)
        self.assertGreaterEqual(len(raised.exception.issues), 3)

    def test_missing_or_duplicate_required_header_is_error(self):
        for value in (None, "姓名"):
            with self.subTest(value=value):
                workbook = valid_workbook()
                workbook["每日统计"]["I3"] = value
                with self.assertRaisesRegex(WorkbookInputError, "表头"):
                    self.read(workbook)

    def test_duplicate_day_and_orphan_raw_record_are_errors(self):
        workbook = valid_workbook()
        daily = workbook["每日统计"]
        daily.append([cell.value for cell in daily[5]])
        workbook["原始记录"].append(["Bob", "u2", "26-08-03 星期一", "2026-08-03 12:00", "正常"])
        with self.assertRaises(WorkbookInputError) as raised:
            self.read(workbook)
        self.assertIn("重复", str(raised.exception))
        self.assertIn("找不到对应", str(raised.exception))

    def test_numeric_identifier_unknown_status_and_formula_are_errors(self):
        workbook = valid_workbook()
        workbook["每日统计"]["B5"] = 12345678901234567890
        workbook["每日统计"]["E5"] = '=TEXT(TIME(9,30,0),"hh:mm")'
        workbook["原始记录"]["E4"] = "位置异常但不知道是否有效"
        with self.assertRaises(WorkbookInputError) as raised:
            self.read(workbook)
        for fragment in ("文本保存", "公式", "未知或空的打卡结果"):
            self.assertIn(fragment, str(raised.exception))

    def test_reversed_fallback_and_unrelated_raw_date_are_errors(self):
        workbook = valid_workbook()
        workbook["每日统计"]["E5"] = "18:00"
        workbook["每日统计"]["G5"] = "09:00"
        workbook["原始记录"]["D5"] = "2026-08-05 20:00"
        with self.assertRaises(WorkbookInputError) as raised:
            self.read(workbook)
        self.assertIn("先后顺序", str(raised.exception))
        self.assertIn("考勤当天或次日", str(raised.exception))

    def test_date_and_time_must_match_the_entire_value(self):
        for value in ("garbage 2026-08-03", "2026-08-03 or 2026-08-04", "2026-08-03 星期五", "2026-02-30"):
            with self.subTest(value=value):
                self.assertIsNone(parse_date_text(value))
        for value in ("garbage 10:30", "2026-08-04 10:30", "10:30:99", "10:30 trailing"):
            with self.subTest(value=value):
                self.assertIsNone(combine_date_time(date(2026, 8, 3), value))
        self.assertIsNone(parse_datetime("2026-08-03 10:3"))
        self.assertEqual(combine_date_time(date(2026, 8, 3), "次日 01:24:59"), datetime(2026, 8, 4, 1, 24, 59))
        with self.assertRaises(ValueError):
            parse_time("09:30 trailing")

    def test_config_rejects_coercion_and_unknown_keys(self):
        for section, key, value in (("workday", "count_non_workday_overtime", "随便"), ("overtime", "dinner_break_minutes", 2.5), ("workday", "work_minute", 450)):
            with self.subTest(key=key):
                data = AppConfig().to_dict()
                data[section][key] = value
                with self.assertRaises(ValueError):
                    AppConfig.from_dict(data)
