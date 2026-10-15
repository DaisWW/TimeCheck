from dataclasses import replace
from datetime import date, time
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from attendance_analyzer.company_calendar import CompanyHoliday
from attendance_analyzer.config import AppConfig, RULE_FIELDS, find_default_config


ROOT = Path(__file__).resolve().parents[1]


class ConfigTest(unittest.TestCase):
    def load_data(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return AppConfig.load(path)

    def test_template_defaults_runtime_config_validity_and_editor_rule_coverage(self):
        expected = AppConfig()
        self.assertEqual(AppConfig.load(ROOT / "config.example.json"), expected)
        # 正式配置会由 HR 编辑；校验其合法性，不要求公司假和规则始终为默认值。
        AppConfig.load(ROOT / "config.json")
        self.assertEqual(
            {f"{section}.{key}" for section, key, _, _, _ in RULE_FIELDS},
            {f"{section}.{key}" for section, value in expected.to_dict().items() if isinstance(value, dict) for key in value},
        )

    def test_round_trip_preserves_nondefault_rules_dates_and_company_holidays(self):
        data = AppConfig().to_dict()
        data["workday"].update(flex_start_begin="9:05:00", work_minutes=420, count_non_workday_overtime=False, rest_shift_keywords=["休息", "调休班"])
        data["overtime"].update(rounding_mode="ceil", invalid_punch_prefixes=["打卡无效", "无效卡"])
        data["rest_day"]["holiday_dates"] = ["2026-10-01", "2026-10-02"]
        data["company_holidays"] = [{"date": "2026-10-19", "period": "上午", "note": "活动，保留备注"}]
        config = self.load_data(data)
        self.assertEqual(config.workday.flex_start_begin, time(9, 5))
        self.assertEqual(config.workday.work_minutes, 420)
        self.assertFalse(config.workday.count_non_workday_overtime)
        self.assertEqual(config.workday.rest_shift_keywords, ("休息", "调休班"))
        self.assertEqual(config.overtime.rounding_mode, "ceil")
        self.assertEqual(config.rest_day.holiday_dates, (date(2026, 10, 1), date(2026, 10, 2)))
        self.assertEqual(config.company_holidays, (CompanyHoliday(date(2026, 10, 19), "上午", "活动，保留备注"),))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "规则 空格.json"
            config.save(path)
            self.assertEqual(AppConfig.load(path), config)

    def test_utf8_with_or_without_bom(self):
        for encoding in ("utf-8", "utf-8-sig"):
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(AppConfig().to_dict(), ensure_ascii=False), encoding=encoding)
                self.assertEqual(AppConfig.load(path), AppConfig())

    def test_wrong_types_and_ranges_report_file_and_field(self):
        cases = (
            ("workday", "work_minutes", 7.5, "类型"),
            ("workday", "work_minutes", "450", "类型"),
            ("workday", "work_minutes", True, "类型"),
            ("workday", "work_minutes", 0, "大于 0"),
            ("workday", "lunch_break_minutes", -1, "不能小于 0"),
            ("workday", "count_non_workday_overtime", "false", "类型"),
            ("workday", "flex_start_begin", "25:00", "时间"),
            ("workday", "flex_start_begin", "10:01", "早于"),
            ("workday", "rest_shift_keywords", [""], "非空"),
            ("rest_day", "holiday_dates", ["2026-02-30"], "日期"),
            ("rest_day", "holiday_dates", ["26-10-01"], "YYYY-MM-DD"),
            ("rest_day", "holiday_dates", [1], "非空字符串"),
            ("overtime", "rounding_unit_minutes", 0, "大于 0"),
            ("overtime", "rounding_mode", "未知", "取整"),
            ("report", "output_suffix", "../result.xlsx", "后缀"),
        )
        for section, key, value, reason in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError) as raised:
                data = AppConfig().to_dict()
                data[section][key] = value
                self.load_data(data)
            for fragment in ("config.json", f"{section}.{key}", reason):
                self.assertIn(fragment, str(raised.exception))

    def test_missing_and_unknown_keys_are_rejected_instead_of_using_defaults(self):
        for section in (None, "workday"):
            for mode in ("missing", "unknown"):
                with self.subTest(section=section, mode=mode):
                    data = AppConfig().to_dict()
                    target = data if section is None else data[section]
                    if mode == "missing":
                        target.pop(next(iter(target)))
                    else:
                        target["拼错字段"] = 450
                    with self.assertRaisesRegex(ValueError, "未知项.*缺少项"):
                        self.load_data(data)

    def test_duplicate_keys_invalid_json_and_nonstandard_numbers_are_rejected(self):
        for text, reason in (("{bad", "line 1"), ('{"workday":1,"workday":2}', "重复"), ('{"workday":NaN}', "NaN"), ('{"workday":Infinity}', "Infinity"), ("[]", "对象")):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.json"
                path.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, reason) as raised:
                    AppConfig.load(path)
                self.assertIn(str(path), str(raised.exception))

    def test_csv_and_toml_are_rejected(self):
        for suffix in (".csv", ".toml"):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "仅支持 .json"):
                AppConfig.load(Path("config" + suffix))

    def test_failed_atomic_save_and_invalid_values_preserve_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = AppConfig()
            original.save(path)
            before = path.read_bytes()
            changed = replace(original, workday=replace(original.workday, work_minutes=420))
            with patch("attendance_analyzer.config.Path.replace", side_effect=PermissionError("blocked")):
                with self.assertRaises(PermissionError):
                    changed.save(path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            with self.assertRaises(ValueError):
                replace(original, workday=replace(original.workday, work_minutes=-1)).save(path)
            self.assertEqual(path.read_bytes(), before)

    def test_default_lookup_reads_only_json_and_keeps_directory_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current, executable = root / "current", root / "app"
            current.mkdir()
            executable.mkdir()
            paths = [current / "config.json", executable / "config.json"]
            for parent in (current, executable):
                for name in ("config.csv", "config.toml", "config.example.json"):
                    (parent / name).touch()
            for path in paths:
                path.touch()
            with patch("attendance_analyzer.config.Path.cwd", return_value=current), patch.object(sys, "executable", str(executable / "tool.exe")):
                self.assertEqual(find_default_config("custom.json"), Path("custom.json"))
                for path in paths:
                    self.assertEqual(find_default_config(None), path)
                    path.unlink()
                self.assertIsNone(find_default_config(None))


if __name__ == "__main__":
    unittest.main()
