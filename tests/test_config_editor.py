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
from attendance_analyzer.config_editor import CompanyHolidayEditor, RulesEditor, apply_rule_values, holiday_range_dates, rule_form_values
from attendance_analyzer.date_picker import parse_date_text
from attendance_analyzer.models import DailyAttendance
from attendance_analyzer.rules import OvertimeRule


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

    def test_date_range_daily_edits_persist_reopen_and_apply_to_calculations(self):
        editor = self.editor(CompanyHolidayEditor)
        before = self.path.read_bytes()
        dialog = editor.open_range()
        dialog.variables["start"].set("2026-10-20")
        dialog.variables["end"].set("2026-10-22")
        dialog.variables["note"].set("连续放假")
        self.assertIn("共 3 天", dialog.preview.cget("text"))
        dialog.confirm()
        self.assertFalse(dialog.winfo_exists())
        self.assertEqual(self.path.read_bytes(), before)
        editor.rows[1][0]["period"].set("上午")
        editor.rows[3][0]["period"].set("下午")
        editor.rows[3][0]["note"].set("下午提前放假")
        editor.save()
        self.assertTrue(editor.saved)
        saved = AppConfig.load(self.path)
        self.assertEqual([item.period for item in saved.company_holidays], ["上午", "上午", "全天", "下午"])
        reopened = self.editor(CompanyHolidayEditor)
        self.assertFalse(reopened.dirty())
        self.assertEqual(reopened.values(), saved.to_dict()["company_holidays"])
        self.assertEqual(reopened.rows[3][0]["note"].get(), "下午提前放假")
        for item, expected_minutes in zip(saved.company_holidays[1:], (300, 0, 180)):
            day = DailyAttendance("Alice", "u1", item.work_date, "A 09:30-18:30")
            self.assertEqual(OvertimeRule(saved).calculate(day, []).required_work_minutes, expected_minutes)

    def test_overlapping_range_reports_dates_and_leaves_everything_unchanged(self):
        editor = self.editor(CompanyHolidayEditor)
        before = self.path.read_bytes()
        dialog = editor.open_range()
        dialog.variables["start"].set("2026-10-18")
        dialog.variables["end"].set("2026-10-20")
        dialog.variables["period"].set("下午")
        with patch("attendance_analyzer.config_editor.messagebox.showerror") as error:
            dialog.confirm()
        self.assertIn("2026-10-19（上午）", error.call_args.args[1])
        self.assertIn("未加入任何日期", error.call_args.args[1])
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(dialog.variables["start"].get(), "2026-10-18")
        self.assertEqual(editor.values(), self.config.to_dict()["company_holidays"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(editor.dirty())

    def test_range_rejects_bad_input_without_partial_changes(self):
        editor = self.editor(CompanyHolidayEditor)
        for start, end, period in (
            ("2026-10-22", "2026-10-20", "全天"),
            ("2026-02-30", "2026-03-01", "全天"),
            ("2026-01-01", "2027-01-02", "全天"),
            ("2026-10-22", "2026-10-23", "夜里"),
        ):
            with self.subTest(start=start, end=end, period=period):
                with self.assertRaises(ValueError):
                    editor.add_range(start, end, period, "不应加入")
                self.assertEqual(editor.values(), self.config.to_dict()["company_holidays"])

    def test_calendar_selects_leap_day_aligns_range_end_and_restores_grabs(self):
        editor = self.editor(CompanyHolidayEditor)
        dialog = editor.open_range()
        dialog.variables["start"].set("2028-02-01")
        dialog.variables["end"].set("2028-02-28")
        dialog.start_field.open_calendar()
        popup = dialog.start_field.calendar
        self.assertEqual(editor.grab_current(), popup)
        next(button for button in popup.buttons if button.cget("text") == "29").invoke()
        self.assertEqual(dialog.variables["start"].get(), "2028-02-29")
        self.assertEqual(dialog.variables["end"].get(), "2028-02-29")
        self.assertEqual(dialog.start_field.weekday.cget("text"), "周二")
        self.assertIn("共 1 天", dialog.preview.cget("text"))
        self.assertEqual(editor.grab_current(), dialog)
        dialog.end_field.open_calendar()
        dialog.end_field.calendar.cancel()
        self.assertEqual(editor.grab_current(), dialog)
        dialog.cancel()
        self.assertEqual(editor.grab_current(), editor)
        self.assertFalse(editor.dirty())

    def test_calendar_navigates_months_years_and_rejects_invalid_month(self):
        editor = self.editor(CompanyHolidayEditor)
        field = editor.rows[0][1][0]
        field.open_calendar()
        popup = field.calendar
        popup.move_month(2)
        self.assertEqual(popup.month_value(), (2026, 12))
        popup.move_month(1)
        self.assertEqual(popup.month_value(), (2027, 1))
        popup.move_month(-1)
        self.assertEqual(popup.month_value(), (2026, 12))
        for year, month, disabled in ((1, 1, "previous"), (9999, 12, "next")):
            for entry, value in ((popup.year, year), (popup.month, month)):
                entry.delete(0, "end")
                entry.insert(0, str(value))
            popup.render()
            self.assertTrue(getattr(popup, disabled).instate(["disabled"]))
            popup.move_month(-1 if disabled == "previous" else 1)
            self.assertEqual(popup.month_value(), (year, month))
        popup.month.delete(0, "end")
        popup.month.insert(0, "13")
        popup.render()
        self.assertIn("有效年月", popup.hint.cget("text"))
        self.assertTrue(all(button.instate(["disabled"]) for button in popup.buttons))
        popup.year.delete(0, "end")
        popup.year.insert(0, "9" * 100)
        popup.render()
        self.assertIn("有效年月", popup.hint.cget("text"))
        popup.cancel()
        self.assertFalse(editor.dirty())

    def test_daily_calendar_edits_date_preserves_fields_and_save_rejects_conflicts(self):
        editor = self.editor(CompanyHolidayEditor)
        editor.add_range("2026-10-20", "2026-10-20", "下午", "单独备注")
        variables, widgets = editor.rows[1]
        widgets[0].open_calendar()
        widgets[0].calendar.choose(HOLIDAY.work_date)
        self.assertEqual(variables["date"].get(), "2026-10-19")
        self.assertEqual(variables["period"].get(), "下午")
        self.assertEqual(variables["note"].get(), "单独备注")
        before = self.path.read_bytes()
        with patch("attendance_analyzer.config_editor.messagebox.showerror") as error:
            editor.save()
        self.assertIn("重复或冲突", error.call_args.args[1])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(editor.winfo_exists())

    def test_mouse_wheel_over_daily_period_does_not_change_it(self):
        editor = self.editor(CompanyHolidayEditor)
        editor.add_range("2027-01-01", "2027-02-01", "上午", "长列表")
        period = editor.rows[-1][1][1]
        period.event_generate("<MouseWheel>", delta=-120)
        self.assertEqual(editor.rows[-1][0]["period"].get(), "上午")

    def test_closed_range_and_calendar_release_variables_and_keep_parent_changes(self):
        editor = self.editor(CompanyHolidayEditor)
        editor.rows[0][0]["period"].set("下午")
        dialog = editor.open_range()
        references = [weakref.ref(variable) for variable in dialog.variables.values()]
        dialog.variables["note"].set("取消范围")
        dialog.start_field.open_calendar()
        popup = dialog.start_field.calendar
        dialog.destroy()
        self.assertTrue(all(reference() is None for reference in references))
        self.assertIsNone(popup.target)
        self.assertEqual(editor.rows[0][0]["period"].get(), "下午")
        self.assertTrue(editor.dirty())
        self.assertEqual(editor.grab_current(), editor)
        self.assertEqual(AppConfig.load(self.path), self.config)

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
