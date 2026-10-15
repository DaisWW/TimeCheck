import csv
from dataclasses import is_dataclass
from datetime import date, time
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from attendance_analyzer.config import AppConfig, find_default_config


ROOT = Path(__file__).resolve().parents[1]


class ConfigTest(unittest.TestCase):
    def load_csv(self, rows, encoding="utf-8-sig", header=None):
        content = io.StringIO(newline="")
        writer = csv.writer(content)
        writer.writerow(header or ["分类", "配置项", "值", "说明"])
        writer.writerows(rows)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.csv"
            path.write_text(content.getvalue(), encoding=encoding, newline="")
            return AppConfig.load(path)

    def test_shipped_csv_covers_and_preserves_all_current_settings(self):
        expected = AppConfig()
        for name in ("config.csv", "config.example.csv"):
            with self.subTest(name=name):
                path = ROOT / name
                self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), sum(len(vars(section)) for section in vars(expected).values() if is_dataclass(section)))
                self.assertEqual(AppConfig.load(path), expected)

    def test_chinese_values_dates_lists_and_excel_times(self):
        config = self.load_csv([
            ["工作日", "弹性上班开始时间", "9:05:00", "Excel可保存为带秒时间"],
            ["工作日", "每日工作分钟", "420", "7小时"],
            ["工作日", "统计非工作日加班", "否", ""],
            ["工作日", "休息班次关键字", "休息；调休班", ""],
            ["加班", "取整方式", "向上取整", ""],
            ["加班", "无效打卡前缀", "打卡无效、无效卡", ""],
            ["非工作日", "法定节假日日期", "2026-10-01;2026/10/2", ""],
        ])
        self.assertEqual(config.workday.flex_start_begin, time(9, 5))
        self.assertEqual(config.workday.work_minutes, 420)
        self.assertFalse(config.workday.count_non_workday_overtime)
        self.assertEqual(config.workday.rest_shift_keywords, ("休息", "调休班"))
        self.assertEqual(config.overtime.rounding_mode, "ceil")
        self.assertEqual(config.overtime.invalid_punch_prefixes, ("打卡无效", "无效卡"))
        self.assertEqual(config.rest_day.holiday_dates, (date(2026, 10, 1), date(2026, 10, 2)))

    def test_utf8_and_legacy_chinese_excel_encodings(self):
        for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
            with self.subTest(encoding=encoding):
                config = self.load_csv([["工作日", "每日工作分钟", "420", "每天7小时"]], encoding)
                self.assertEqual(config.workday.work_minutes, 420)

    def test_boolean_and_rounding_options(self):
        for value, expected in (("是", True), ("否", False), ("TRUE", True), ("false", False)):
            with self.subTest(value=value):
                config = self.load_csv([["工作日", "统计非工作日加班", value, ""]])
                self.assertIs(config.workday.count_non_workday_overtime, expected)
        for value, expected in (("向下取整", "floor"), ("向上取整", "ceil"), ("就近取整", "nearest"), ("nearest", "nearest")):
            with self.subTest(value=value):
                config = self.load_csv([["加班", "取整方式", value, ""]])
                self.assertEqual(config.overtime.rounding_mode, expected)

    def test_quoted_commas_newlines_reordered_headers_and_blank_rows(self):
        config = self.load_csv([
            ["多行说明\n不影响配置", "2026-10-01,2026-10-02", "法定节假日日期", "非工作日"],
            [],
            ["带逗号,的说明", "_分析,复核.xlsx", "输出文件后缀", "报表"],
        ], header=["说明", "值", "配置项", "分类"])
        self.assertEqual(config.rest_day.holiday_dates, (date(2026, 10, 1), date(2026, 10, 2)))
        self.assertEqual(config.report.output_suffix, "_分析,复核.xlsx")

    def test_blank_lists_are_empty_and_missing_fields_use_defaults(self):
        config = self.load_csv([
            ["非工作日", "法定节假日日期", "", ""],
            ["工作日", "休息班次关键字", "", ""],
            ["加班", "无效打卡前缀", "", ""],
        ])
        self.assertEqual(config.rest_day.holiday_dates, ())
        self.assertEqual(config.workday.rest_shift_keywords, ())
        self.assertEqual(config.overtime.invalid_punch_prefixes, ())
        self.assertEqual(config.workday.work_minutes, 450)

    def test_invalid_values_report_file_line_and_original_value(self):
        cases = [
            ("工作日", "每日工作分钟", "7.5", "整数"),
            ("工作日", "每日工作分钟", "450分钟", "整数"),
            ("工作日", "每日工作分钟", "=450", "整数"),
            ("工作日", "每日工作分钟", "", "不能为空"),
            ("工作日", "每日工作分钟", "0", "大于 0"),
            ("工作日", "午休分钟", "-1", "不能小于 0"),
            ("工作日", "统计非工作日加班", "随便", "开关"),
            ("工作日", "弹性上班开始时间", "25:00", "时间"),
            ("工作日", "弹性上班开始时间", "10:01", "早于"),
            ("工作日", "休息班次关键字", "休息;;调休", "空项"),
            ("非工作日", "法定节假日日期", "2026-02-30", "日期"),
            ("非工作日", "法定节假日日期", "26-10-01", "日期"),
            ("非工作日", "法定节假日日期", "2026-10-01 trailing", "日期"),
            ("加班", "取整单位分钟", "0", "大于 0"),
            ("加班", "取整方式", "四舍五入", "取整"),
            ("报表", "输出文件后缀", "../result.xlsx", "后缀"),
        ]
        for section, key, value, reason in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError) as raised:
                self.load_csv([[section, key, value, ""]])
            message = str(raised.exception)
            for fragment in ("config.csv", "第 2 行", "原值", value, reason):
                self.assertIn(fragment, message)

    def test_unknown_duplicate_and_missing_columns_are_errors(self):
        for rows, reason in (
            ([["工作日", "工作时间", "450", ""]], "未知"),
            ([["工作日", "每日工作分钟", "450", ""], ["工作日", "每日工作分钟", "420", ""]], "重复"),
            ([["工作日", "每日工作分钟", "450"]], "4列"),
            ([], "没有配置项"),
        ):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, reason):
                self.load_csv(rows)
        with self.assertRaisesRegex(ValueError, "表头"):
            self.load_csv([], header=["分类", "配置项", "值", "值"])

    def test_malformed_csv_and_unsupported_extension_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.csv"
            path.write_text('分类,配置项,值,说明\n工作日,每日工作分钟,"450', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "CSV 格式错误"):
                AppConfig.load(path)
            for suffix in (".json", ".toml"):
                with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "仅支持 .csv"):
                    AppConfig.load(path.with_suffix(suffix))

    def test_default_lookup_reads_only_csv_and_keeps_directory_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "current"
            executable = root / "app"
            current.mkdir()
            executable.mkdir()
            paths = [current / "config.csv", executable / "config.csv"]
            for parent in (current, executable):
                (parent / "config.toml").touch()
                (parent / "config.example.csv").touch()
            for path in paths:
                path.touch()
            with patch("attendance_analyzer.config.Path.cwd", return_value=current), patch.object(sys, "executable", str(executable / "tool.exe")):
                self.assertEqual(find_default_config("custom.csv"), Path("custom.csv"))
                for path in paths:
                    self.assertEqual(find_default_config(None), path)
                    path.unlink()
                self.assertIsNone(find_default_config(None))


if __name__ == "__main__":
    unittest.main()
