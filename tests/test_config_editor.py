from dataclasses import replace
from datetime import date
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
import weakref

from attendance_analyzer.company_calendar import CompanyHoliday
from attendance_analyzer.config import AppConfig
from attendance_analyzer.config_editor import CompanyHolidayEditor, RulesEditor, apply_rule_values, rule_form_values


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


@unittest.skipUnless(sys.platform == "win32", "Tk 桌面弹窗集成测试在 Windows 执行")
class ConfigDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "中文 规则.json"
        self.config = AppConfig(company_holidays=(HOLIDAY,))
        self.config.save(self.path)
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if dialog.winfo_exists():
                dialog.destroy()
        self.directory.cleanup()

    def editor(self, editor_type):
        editor = editor_type(self.root, self.path, AppConfig.load(self.path))
        editor.withdraw()
        self.dialogs.append(editor)
        return editor

    def test_rule_save_preserves_latest_company_holidays_and_reopens_with_values(self):
        editor = self.editor(RulesEditor)
        editor.variables["workday.work_minutes"].set("420")
        latest = CompanyHoliday(date(2026, 10, 20), "下午", "新安排")
        replace(self.config, company_holidays=(latest,)).save(self.path)
        editor.save()
        self.assertTrue(editor.saved)
        result = AppConfig.load(self.path)
        self.assertEqual(result.workday.work_minutes, 420)
        self.assertEqual(result.company_holidays, (latest,))
        reopened = self.editor(RulesEditor)
        self.assertEqual(reopened.variables["workday.work_minutes"].get(), "420")
        self.assertFalse(reopened.dirty())

    def test_invalid_rule_stays_open_focuses_field_and_does_not_write(self):
        editor = self.editor(RulesEditor)
        before = self.path.read_bytes()
        editor.variables["workday.work_minutes"].set("7.5")
        with patch("attendance_analyzer.config_editor.messagebox.showerror") as error:
            editor.save()
        self.assertIn("每日工作分钟", error.call_args.args[1])
        self.assertTrue(editor.winfo_exists())
        self.assertFalse(editor.saved)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(editor.notebook.select(), str(editor.widgets["workday.work_minutes"][1]))

    def test_cancel_choices_keep_editing_discard_or_save(self):
        for answer in (None, False, True):
            with self.subTest(answer=answer):
                self.config.save(self.path)
                editor = self.editor(RulesEditor)
                editor.variables["workday.work_minutes"].set("420")
                with patch("attendance_analyzer.config_editor.messagebox.askyesnocancel", return_value=answer):
                    editor.cancel()
                self.assertEqual(bool(editor.winfo_exists()), answer is None)
                self.assertEqual(editor.saved, answer is True)
                self.assertEqual(AppConfig.load(self.path).workday.work_minutes, 420 if answer is True else 450)

    def test_save_permission_failure_keeps_original_and_dialog_open(self):
        editor = self.editor(RulesEditor)
        editor.variables["workday.work_minutes"].set("420")
        before = self.path.read_bytes()
        with patch("attendance_analyzer.config.Path.replace", side_effect=PermissionError("blocked")), patch("attendance_analyzer.config_editor.messagebox.showerror") as error:
            editor.save()
        self.assertIn("原配置保留", error.call_args.args[1])
        self.assertTrue(editor.winfo_exists())
        self.assertFalse(editor.saved)
        self.assertEqual(self.path.read_bytes(), before)

    def test_company_holiday_rows_save_additions_edits_and_deletions_preserving_rules(self):
        editor = self.editor(CompanyHolidayEditor)
        editor.rows[0][0]["period"].set("下午")
        editor.add_row({"date": "2026-10-20", "period": "全天", "note": " 备注保留空格 "})
        editor.add_row({"date": "2026-10-21", "period": "上午", "note": "删除这项"})
        editor.delete_row(editor.rows[-1])
        replace(self.config, workday=replace(self.config.workday, work_minutes=420)).save(self.path)
        editor.save()
        self.assertTrue(editor.saved)
        result = AppConfig.load(self.path)
        self.assertEqual(result.workday.work_minutes, 420)
        self.assertEqual(result.company_holidays, (
            replace(HOLIDAY, period="下午"), CompanyHoliday(date(2026, 10, 20), "全天", " 备注保留空格 "),
        ))
        reopened = self.editor(CompanyHolidayEditor)
        self.assertEqual(len(reopened.rows), 2)
        self.assertFalse(reopened.dirty())

    def test_bad_or_duplicate_holiday_dates_do_not_write_and_keep_rows(self):
        editor = self.editor(CompanyHolidayEditor)
        editor.add_row()
        before = self.path.read_bytes()
        for value in ("", "2026-02-30", "2026-10-19"):
            with self.subTest(value=value):
                editor.rows[-1][0]["date"].set(value)
                with patch("attendance_analyzer.config_editor.messagebox.showerror") as error:
                    editor.save()
                self.assertIn("第 2 项", error.call_args.args[1])
                self.assertTrue(editor.winfo_exists())
                self.assertFalse(editor.saved)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(len(editor.rows), 2)

    def test_deleting_all_holidays_saves_empty_calendar(self):
        editor = self.editor(CompanyHolidayEditor)
        editor.delete_row(editor.rows[0])
        editor.save()
        self.assertEqual(AppConfig.load(self.path).company_holidays, ())
        self.assertTrue(editor.saved)

    def test_closed_dialog_releases_tk_variables_before_worker_garbage_collection(self):
        for editor_type in (RulesEditor, CompanyHolidayEditor):
            with self.subTest(editor_type=editor_type):
                editor = self.editor(editor_type)
                variables = editor.variables if isinstance(editor, RulesEditor) else editor.rows[0][0]
                references = [weakref.ref(variable) for variable in variables.values()]
                del variables
                editor.destroy()
                self.assertTrue(all(reference() is None for reference in references))


if __name__ == "__main__":
    unittest.main()
