from dataclasses import replace
from datetime import date
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from attendance_analyzer.company_calendar import CompanyHoliday
from attendance_analyzer.config import AppConfig
from attendance_analyzer.web_api import WebApi
from test_input_validation import valid_workbook


class WebApiTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.path = self.base / "config.json"
        AppConfig().save(self.path)
        self.api = WebApi(self.base)

    def tearDown(self):
        self.api.close()
        self.temporary.cleanup()

    def ok(self, result):
        self.assertTrue(result["ok"], result)
        return result.get("data")

    def upload(self, files):
        plan = self.ok(self.api.begin_upload([{"name": name, "size": len(content)} for name, content in files]))
        for index, (_, content) in enumerate(files):
            self.api._receive_upload(plan["token"], index, io.BytesIO(content), len(content))
        return plan

    def test_first_list_creates_missing_directories_without_config_error(self):
        self.assertFalse((self.base / "input").exists())
        self.assertFalse((self.base / "output").exists())
        files = self.ok(self.api.files())
        self.assertEqual(files, {"input": [], "output": [], "config_error": None})
        self.assertTrue((self.base / "input").is_dir())
        self.assertTrue((self.base / "output").is_dir())

    def test_fixed_directories_ignore_old_paths_and_list_all_files(self):
        (self.base / "window_paths.json").write_text('{"config":"wrong"}')
        name = "研发中心_2026年09月_含 空格{括号}的超长考勤名称.xlsx"
        plan = self.upload([(name, b"original"), ("乙部门.xlsm", b"other")])
        self.assertEqual(self.ok(self.api.import_files(plan["token"], "skip")), {"copied": 2, "skipped": 0})
        (self.base / "input/说明.txt").touch()
        files = self.ok(self.api.files())
        self.assertEqual(sum(item["eligible"] for item in files["input"]), 2)
        self.assertEqual((self.base / "input" / name).read_bytes(), b"original")
        self.assertTrue((self.base / "output").is_dir())
        self.assertIsNone(files["config_error"])

    def test_bad_format_duplicate_name_or_traversal_rejects_entire_selection(self):
        for files in [
            [{"name": "新.xlsx", "size": 1}, {"name": "说明.txt", "size": 1}],
            [{"name": "部门.xlsx", "size": 1}, {"name": "部门.XLSX", "size": 1}],
            [{"name": "../逃逸.xlsx", "size": 0}], [{"name": "C:\\外部.xlsx", "size": 0}],
            [{"name": "~$临时.xlsx", "size": 0}], [{"name": "旧_加班分析.xlsx", "size": 0}],
            [{"name": "bad.xlsx", "size": True}],
        ]:
            with self.subTest(files=files):
                self.assertFalse(self.api.begin_upload(files)["ok"])
                self.assertFalse(list((self.base / "input").glob("*.xlsx")) if (self.base / "input").exists() else [])

    def test_incomplete_or_wrong_size_upload_never_copies_placeholder(self):
        plan = self.ok(self.api.begin_upload([{"name": "a.xlsx", "size": 3}]))
        self.assertFalse(self.api.import_files(plan["token"], "skip")["ok"])
        with self.assertRaisesRegex(ValueError, "大小不符"):
            self.api._receive_upload(plan["token"], 0, io.BytesIO(b"abc"), 2)
        with self.assertRaisesRegex(ValueError, "中断"):
            self.api._receive_upload(plan["token"], 0, io.BytesIO(b"ab"), 3)
        self.assertFalse(self.api.import_files(plan["token"], "replace")["ok"])
        self.assertFalse((self.base / "input/a.xlsx").exists())
        self.ok(self.api.import_files(plan["token"], "cancel"))
        self.assertFalse(self.api._uploads)

    def test_duplicate_skip_cancel_replace_and_failed_replace_preserve_old_file(self):
        plan = self.upload([("考勤.xlsx", b"old")])
        self.ok(self.api.import_files(plan["token"], "skip"))
        target = self.base / "input/考勤.xlsx"
        for choice in ("cancel", "skip"):
            plan = self.upload([("考勤.xlsx", b"new")])
            self.assertEqual(plan["duplicates"], ["考勤.xlsx"])
            self.ok(self.api.import_files(plan["token"], choice))
            self.assertEqual(target.read_bytes(), b"old")
        plan = self.upload([("考勤.xlsx", b"new")])
        with patch("attendance_analyzer.file_manager.shutil.copyfile", side_effect=PermissionError("文件占用")):
            result = self.api.import_files(plan["token"], "replace")
        self.assertFalse(result["ok"])
        self.assertIn("0/1", result["error"])
        self.assertEqual(target.read_bytes(), b"old")
        self.ok(self.api.import_files(plan["token"], "replace"))
        self.assertEqual(target.read_bytes(), b"new")

    def test_interrupted_retry_and_changed_staging_file_cannot_import(self):
        plan = self.upload([("a.xlsx", b"complete")])
        with self.assertRaisesRegex(ValueError, "中断"):
            self.api._receive_upload(plan["token"], 0, io.BytesIO(b"part"), len(b"complete"))
        self.assertFalse(self.api.import_files(plan["token"], "skip")["ok"])
        self.assertFalse((self.base / "input/a.xlsx").exists())
        self.api._receive_upload(plan["token"], 0, io.BytesIO(b"complete"), len(b"complete"))
        self.api._imports[plan["token"]][0][0][0].write_bytes(b"changed")
        self.assertFalse(self.api.import_files(plan["token"], "skip")["ok"])
        self.assertFalse((self.base / "input/a.xlsx").exists())

    def test_job_finishes_without_browser_polling(self):
        plan = self.upload([("考勤.xlsx", b"invalid workbook")])
        self.ok(self.api.import_files(plan["token"], "skip"))
        self.ok(self.api.start())
        deadline = time.monotonic() + 5
        while self.api._running and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(self.api._running)
        self.assertEqual(self.api._phase, "error")
        self.assertTrue(self.api.close()["ok"])

    def test_exit_prevents_new_writes_before_http_server_stops(self):
        self.ok(self.api.close())
        for result in (self.api.start(), self.api.begin_upload([{"name": "a.xlsx", "size": 0}]),
                       self.api.preview_recycle("input", [], True)):
            self.assertFalse(result["ok"])
            self.assertIn("已退出", result["error"])
        self.ok(self.api.close())

    def test_confirmation_rejects_changed_target_and_expired_token(self):
        plan = self.upload([("a.xlsx", b"new")])
        (self.base / "input/a.xlsx").write_bytes(b"created after confirmation")
        self.assertIn("发生了变化", self.api.import_files(plan["token"], "replace")["error"])
        self.assertFalse(self.api.import_files("unknown", "replace")["ok"])
        self.assertEqual((self.base / "input/a.xlsx").read_bytes(), b"created after confirmation")

    def test_recycle_requires_token_rechecks_paths_and_deduplicates_parent_children(self):
        self.ok(self.api.files())
        batch = self.base / "output/统计_历史"
        batch.mkdir()
        (batch / "结果.xlsx").touch()
        plan = self.ok(self.api.preview_recycle("output", ["统计_历史", "统计_历史/结果.xlsx"]))
        self.assertEqual(plan["names"], ["统计_历史（包含其中全部文件）"])
        with patch("attendance_analyzer.file_manager.send2trash") as recycle:
            self.assertFalse(self.api.recycle("no confirmation")["ok"])
            recycle.assert_not_called()
            self.assertEqual(self.ok(self.api.recycle(plan["token"])), 1)
            recycle.assert_called_once_with(str(batch))
        self.assertFalse(self.api.preview_recycle("output", ["../config.json"])["ok"])
        self.assertFalse(self.api.preview_recycle("output", ["."])["ok"])
        self.assertFalse(self.api.preview_recycle("unknown", [], True)["ok"])

    def test_recycle_changed_file_and_empty_selection_fail(self):
        plan = self.upload([("a.xlsx", b"old")])
        self.ok(self.api.import_files(plan["token"], "skip"))
        plan = self.ok(self.api.preview_recycle("input", [], True))
        (self.base / "input/a.xlsx").write_bytes(b"updated")
        with patch("attendance_analyzer.web_api.recycle_entries") as recycle:
            self.assertFalse(self.api.recycle(plan["token"])["ok"])
            recycle.assert_not_called()
        self.assertFalse(self.api.preview_recycle("input", [])["ok"])

    def test_open_is_limited_to_managed_entries_and_known_targets(self):
        self.ok(self.api.files())
        with patch("attendance_analyzer.web_api.os.startfile") as startfile:
            self.ok(self.api.open_path("input"))
            startfile.assert_called_once_with(self.base / "input")
            for kind, identifier in [("input", "../config.json"), ("result", None), ("log", None), ("secret", None)]:
                self.assertFalse(self.api.open_path(kind, identifier)["ok"])

    def test_configuration_error_is_visible_and_never_uses_defaults(self):
        self.path.write_text("broken", encoding="utf-8")
        self.assertIn("config.json", self.ok(self.api.files())["config_error"])
        with patch("attendance_analyzer.web_api.Thread") as worker:
            self.assertFalse(self.api.start()["ok"])
            worker.assert_not_called()
        self.assertFalse(self.api.begin_upload([{"name": "a.xlsx", "size": 0}])["ok"])
        self.assertFalse(self.api.get_settings("rules")["ok"])

    def test_running_and_copying_lock_every_mutation_and_close(self):
        for attribute in ("_running", "_busy"):
            setattr(self.api, attribute, True)
            for method, args in [(self.api.start, ()), (self.api.get_settings, ("rules",)),
                                 (self.api.close, ()), (self.api.preview_recycle, ("input", [])),
                                 (self.api.begin_upload, ([{"name": "a.xlsx", "size": 0}],)),
                                 (self.api.import_files, ("token", "cancel")),
                                 (self.api.add_holiday_range, ([], "2026-10-01", "2026-10-01", "全天", ""))]:
                self.assertIn("正在处理", method(*args)["error"])
            self.assertTrue(self.ok(self.api.poll())["busy"])
            setattr(self.api, attribute, False)

    def test_real_background_batch_and_failure_preserve_success_and_full_error(self):
        source = self.base / "外部原始文件"
        source.mkdir()
        contents = []
        for name in ("1月.xlsx", "2月.xlsm"):
            path = source / name
            workbook = valid_workbook()
            workbook.save(path)
            workbook.close()
            contents.append((name, path.read_bytes()))
        broken = valid_workbook()
        broken["每日统计"]["I5"] = "事假 特殊格式"
        broken.save(source / "3月.xlsx")
        broken.close()
        contents.append(("3月.xlsx", (source / "3月.xlsx").read_bytes()))
        plan = self.upload(contents)
        self.ok(self.api.import_files(plan["token"], "skip"))
        run_id = self.ok(self.api.start())
        deadline = time.monotonic() + 15
        status = self.ok(self.api.poll())
        while status["running"] and time.monotonic() < deadline:
            time.sleep(0.02)
            status = self.ok(self.api.poll())
        self.assertFalse(status["running"])
        self.assertEqual((status["phase"], status["completed"], status["total"]), ("error", 2, 3))
        self.assertIn("事假 特殊格式", status["log"])
        self.assertIn("每日统计 第 5 行", status["log"])
        self.assertIn("退出码 4", status["log"])
        self.assertTrue(status["result"])
        self.assertTrue(status["log_available"])
        self.assertEqual(len(self.ok(self.api.files())["output"][0]["children"]), 2)
        incremental = self.ok(self.api.poll(status["cursor"], run_id))
        self.assertEqual(incremental["log"], "")
        self.assertEqual(self.ok(self.api.poll(999999, "stale"))["log"], status["log"])
        for name, content in contents:
            self.assertEqual((source / name).read_bytes(), content)
            self.assertEqual((self.base / "input" / name).read_bytes(), content)


class WebSettingsTest(unittest.TestCase):
    setUp = WebApiTest.setUp
    tearDown = WebApiTest.tearDown
    ok = WebApiTest.ok

    def test_rule_save_preserves_latest_holidays_and_reopens(self):
        form = self.ok(self.api.get_settings("rules"))
        self.assertEqual(len(form["fields"]), 22)
        changed = replace(AppConfig(), company_holidays=(CompanyHoliday(date(2026, 10, 19), "上午", "活动"),))
        changed.save(self.path)
        form["values"]["overtime.dinner_break_minutes"] = "0"
        self.ok(self.api.save_settings("rules", form["values"], form["revision"]))
        saved = AppConfig.load(self.path)
        self.assertEqual(saved.company_holidays, changed.company_holidays)
        self.assertEqual(saved.overtime.dinner_break_minutes, 0)
        self.assertEqual(self.ok(WebApi(self.base).get_settings("rules"))["values"], form["values"])

    def test_invalid_rules_missing_extra_wrong_types_and_permissions_keep_original(self):
        form = self.ok(self.api.get_settings("rules"))
        before = self.path.read_bytes()
        for values in ({**form["values"], "workday.work_minutes": "7.5"},
                       {**form["values"], "extra": "1"}, {},
                       {**form["values"], "workday.work_minutes": 450}):
            self.assertFalse(self.api.save_settings("rules", values, form["revision"])["ok"])
            self.assertEqual(self.path.read_bytes(), before)
        form["values"]["workday.work_minutes"] = "420"
        with patch.object(AppConfig, "save", side_effect=PermissionError("文件占用")):
            self.assertIn("文件占用", self.api.save_settings("rules", form["values"], form["revision"])["error"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_stale_same_section_revision_rejects_overwrite(self):
        for kind in ("rules", "holidays"):
            first = self.ok(self.api.get_settings(kind))
            second = self.ok(self.api.get_settings(kind))
            values = ({**second["values"], "workday.work_minutes": "420"} if kind == "rules" else
                      [{"date": "2026-10-19", "period": "全天", "note": "外部修改"}])
            self.ok(self.api.save_settings(kind, values, second["revision"]))
            before = self.path.read_bytes()
            self.assertIn("已被", self.api.save_settings(kind, first["values"], first["revision"])["error"])
            self.assertEqual(self.path.read_bytes(), before)

    def test_holiday_ranges_can_edit_each_day_and_preserve_latest_rules(self):
        form = self.ok(self.api.get_settings("holidays"))
        rows = self.ok(self.api.add_holiday_range(form["values"], "2026-12-31", "2027-01-02", "全天", "跨年"))
        rows[0]["period"] = "下午"
        rows[-1].update(date="2027-01-03", period="上午", note="最后半天")
        rules = self.ok(self.api.get_settings("rules"))
        rules["values"]["overtime.dinner_break_minutes"] = "0"
        self.ok(self.api.save_settings("rules", rules["values"], rules["revision"]))
        self.ok(self.api.save_settings("holidays", rows, form["revision"]))
        saved = AppConfig.load(self.path)
        self.assertEqual(saved.overtime.dinner_break_minutes, 0)
        self.assertEqual(saved.to_dict()["company_holidays"], rows)
        self.assertEqual(self.ok(WebApi(self.base).get_settings("holidays"))["values"], rows)
        latest = self.ok(self.api.get_settings("holidays"))
        self.ok(self.api.save_settings("holidays", [], latest["revision"]))
        self.assertEqual(AppConfig.load(self.path).company_holidays, ())

    def test_bad_ranges_and_conflicts_keep_draft_and_disk_unchanged(self):
        rows = [{"date": "2026-10-20", "period": "上午", "note": "已有安排"}]
        before = self.path.read_bytes()
        for start, end, period in [("2026-10-19", "2026-10-21", "全天"),
                                   ("2026-10-21", "2026-10-19", "上午"),
                                   ("2026-02-30", "2026-03-01", "下午"),
                                   ("2026-01-01", "2027-01-02", "全天"),
                                   ("2026-10-21", "2026-10-21", "半天")]:
            result = self.api.add_holiday_range(rows, start, end, period, "备注")
            self.assertFalse(result["ok"])
            self.assertEqual(rows, [{"date": "2026-10-20", "period": "上午", "note": "已有安排"}])
            self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_holiday_rows_never_save_and_empty_calendar_is_valid(self):
        form = self.ok(self.api.get_settings("holidays"))
        before = self.path.read_bytes()
        row = {"date": "2026-10-20", "period": "上午", "note": "活动"}
        for rows in [[row, row], [{**row, "date": "2026-02-30"}], [{**row, "period": "半天"}], [{**row, "note": 1}]]:
            self.assertFalse(self.api.save_settings("holidays", rows, form["revision"])["ok"])
            self.assertEqual(self.path.read_bytes(), before)
