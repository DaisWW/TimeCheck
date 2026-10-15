from contextlib import redirect_stdout
from datetime import date
import gc
import io
from pathlib import Path
from queue import Queue
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

from attendance_analyzer import gui
from attendance_analyzer.config import AppConfig
from attendance_analyzer.excel_reader import WorkbookInputError
from attendance_analyzer.models import InputIssue
from test_input_validation import valid_workbook


def drain(events):
    items = []
    while not events.empty():
        items.append(events.get_nowait())
    return items


class GuiJobTest(unittest.TestCase):
    def test_missing_json_stops_before_analysis_and_saves_readable_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = Queue()
            outer_output = io.StringIO()
            with patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer, redirect_stdout(outer_output):
                gui.run_job(root / "input", root / "output", root / "config.json", root / "logs", events)
                print("stdout restored")
            analyzer.assert_not_called()
            items = drain(events)
            self.assertEqual(items[-1][0:2], ("finished", 2))
            self.assertIn("config.json", items[-1][2].read_text(encoding="utf-8-sig"))
            self.assertEqual(outer_output.getvalue(), "stdout restored\n")

    def test_batch_reports_progress_and_stops_on_invalid_file_with_source_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            AppConfig().save(config)
            input_dir = root / "input"
            input_dir.mkdir()
            paths = [input_dir / f"{index}月.xlsx" for index in range(1, 4)]
            for path in paths:
                path.touch()
            issue = InputIssue("每日统计", 5, "Alice", "u1", date(2026, 8, 3), "关联的审批单", "事假 特殊格式", "审批格式无法解析", "停止统计", "修正审批")
            events = Queue()
            output_dir = root / "output" / "本次"
            with patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer_class, patch("attendance_analyzer.cli.OvertimeReportWriter") as writer_class:
                analyzer = analyzer_class.return_value
                analyzer.last_input_issues = []
                analyzer.analyze_overtime.side_effect = [[], WorkbookInputError(paths[1], [issue])]
                gui.run_job(input_dir, output_dir, config, root / "logs", events)
                self.assertEqual([call.args[0] for call in analyzer.analyze_overtime.call_args_list], paths[:2])
                writer_class.return_value.write.assert_called_once_with(output_dir / "1月_加班分析.xlsx", [], [])
            items = drain(events)
            self.assertIn(("progress", 1, 3, "已完成：1月.xlsx"), items)
            self.assertEqual(items[-1][:2], ("finished", 4))
            self.assertEqual(items[-1][3:], (1, 3))
            log_text = items[-1][2].read_text(encoding="utf-8-sig")
            for fragment in ("每日统计 第 5 行", "事假 特殊格式", str(output_dir), "退出码 4"):
                self.assertIn(fragment, log_text)

    def test_directory_success_processes_all_workbooks_into_selected_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            AppConfig().save(config)
            input_dir = root / "每月 考勤"
            input_dir.mkdir()
            inputs = [input_dir / "一月.xlsx", input_dir / "二月.xlsm"]
            for path in inputs + [input_dir / "~$临时.xlsx", input_dir / "历史_加班分析.xlsx", input_dir / "说明.txt"]:
                path.touch()
            (input_dir / "子目录").mkdir()
            (input_dir / "子目录/三月.xlsx").touch()
            output_dir = root / "自选 输出目录" / "本次结果"
            events = Queue()
            with patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer, patch("attendance_analyzer.cli.OvertimeReportWriter") as writer:
                analyzer.return_value.analyze_overtime.return_value = []
                analyzer.return_value.last_input_issues = []
                gui.run_job(input_dir, output_dir, config, root / "logs", events)
                self.assertEqual(analyzer.return_value.analyze_overtime.call_args_list, [call(path) for path in inputs])
                self.assertEqual(writer.return_value.write.call_args_list, [call(output_dir / f"{path.stem}_加班分析.xlsx", [], []) for path in inputs])
            items = drain(events)
            self.assertEqual(items[-1][0:2], ("finished", 0))
            self.assertEqual(items[-1][3:], (2, 2))
            self.assertIn(("progress", 2, 2, "已完成：二月.xlsm"), items)

    def test_unexpected_failure_is_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = Queue()
            with patch("attendance_analyzer.gui.cli.main", side_effect=RuntimeError("unexpected detail")):
                gui.run_job(root / "input", root / "output", root / "config.json", root / "logs", events)
            items = drain(events)
            self.assertEqual(items[-1][0:2], ("finished", 5))
            self.assertIn("unexpected detail", items[-1][2].read_text(encoding="utf-8-sig"))
            self.assertIn("尚未完成", gui.outcome_message(5, 0, 0))

    def test_unwritable_log_location_fails_before_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blocked = root / "logs"
            blocked.write_text("file blocks directory", encoding="utf-8")
            events = Queue()
            with patch("attendance_analyzer.gui.cli.main") as main:
                gui.run_job(root / "input", root / "output", root / "config.json", blocked, events)
            main.assert_not_called()
            items = drain(events)
            self.assertIn("无法保存运行日志", items[0][1])
            self.assertEqual(items[-1][0:2], ("finished", 3))


@unittest.skipUnless(sys.platform == "win32", "Tk 文件管理集成测试在 Windows 执行")
class GuiFileListTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        AppConfig().save(self.base / "config.json")
        # 旧路径记录不会改变固定的数据位置。
        (self.base / "window_paths.json").write_text('{"config":"wrong"}', encoding="utf-8")
        self.root = gui.create_root()
        self.root.withdraw()
        self.errors = patch.object(gui.messagebox, "showerror").start()
        self.window = gui.AttendanceWindow(self.root, self.base)
        self.root.update_idletasks()
        self.source = self.base / "外部 原始考勤"
        self.source.mkdir()

    def tearDown(self):
        self.window.close()
        self.window = self.root = None
        gc.collect()
        patch.stopall()
        self.temporary.cleanup()

    def file(self, name="甲部门 考勤.xlsx", content=b"source"):
        path = self.source / name
        path.write_bytes(content)
        return path

    def test_fixed_directories_and_file_picker_copy_all_inputs_to_list(self):
        sources = [self.file(), self.file("乙部门.xlsm")]
        with patch.object(gui.filedialog, "askopenfilenames", return_value=[str(path) for path in sources]):
            self.window.select_files()
        self.assertEqual(self.window.config_path, self.base / "config.json")
        self.assertEqual(set(self.window.tree_paths["input"]), {path.name for path in sources})
        self.assertEqual(self.window.input_summary.get(), "待统计 2 个文件")
        for path in sources:
            self.assertEqual((self.base / "input" / path.name).read_bytes(), path.read_bytes())
        self.assertEqual((self.base / "window_paths.json").read_text(), '{"config":"wrong"}')
        self.assertFalse(self.window.details_visible.get())
        self.errors.assert_not_called()

    def test_minimum_window_keeps_file_rows_visible_with_details_and_long_names_scroll(self):
        self.window.import_files([self.file("研发中心_2026年09月考勤汇总_原始导出数据.xlsx")])
        self.root.geometry("900x620")
        self.window.details_visible.set(True)
        self.window.toggle_details()
        self.root.deiconify()
        self.root.update()
        for tree in self.window.trees.values():
            self.assertGreaterEqual(tree.winfo_height(), 60)
        tree = self.window.trees["input"]
        tree.xview_moveto(1)
        self.assertGreater(tree.xview()[0], 0)
        self.assertEqual(len(tree.selection()), 0)

    def test_drop_handles_tcl_quoted_unicode_spaces_and_rejects_bad_input_without_partial_copy(self):
        paths = [self.file(), self.file("带 {括号} 文件.xlsm")]
        data = self.root.tk.call("list", *(str(path) for path in paths))
        self.assertEqual(self.window.drop_files(SimpleNamespace(data=data)), gui.COPY)
        self.assertEqual(len(self.window.tree_paths["input"]), 2)
        bad, good = self.file("说明.txt"), self.file("新考勤.xlsx")
        data = self.root.tk.call("list", str(bad), str(good))
        self.assertEqual(self.window.drop_files(SimpleNamespace(data=data)), gui.REFUSE_DROP)
        self.assertFalse((self.base / "input" / good.name).exists())
        self.assertTrue(self.window.details_visible.get())
        self.errors.assert_called_once()

    def test_same_name_can_skip_cancel_or_replace_and_copy_failure_keeps_old_file(self):
        path = self.file()
        self.window.import_files([path])
        target = self.base / "input" / path.name
        path.write_bytes(b"updated")
        for choice in (False, None):
            with patch.object(gui.messagebox, "askyesnocancel", return_value=choice):
                self.window.import_files([path])
            self.assertEqual(target.read_bytes(), b"source")
        with patch.object(gui.messagebox, "askyesnocancel", return_value=True):
            self.assertTrue(self.window.import_files([path]))
        self.assertEqual(target.read_bytes(), b"updated")

    def test_cleanup_requires_confirmation_and_selected_batch_includes_children_only_once(self):
        self.window.import_files([self.file()])
        batch = self.base / "output" / "统计_历史"
        batch.mkdir()
        report = batch / "结果.xlsx"
        report.touch()
        self.window.refresh_files()
        tree = self.window.trees["output"]
        tree.selection_set("统计_历史", "统计_历史/结果.xlsx")
        with patch.object(gui.messagebox, "askyesno", return_value=False), patch.object(gui, "recycle_entries") as recycle:
            self.window.remove_files("output")
            recycle.assert_not_called()
        with patch.object(gui.messagebox, "askyesno", return_value=True) as confirm, patch("attendance_analyzer.file_manager.send2trash") as recycle:
            self.window.remove_files("output")
            recycle.assert_called_once_with(str(batch))
            self.assertIn("全部文件", confirm.call_args.args[1])
        with patch.object(gui.messagebox, "askyesno", return_value=True), patch.object(gui, "recycle_entries", return_value=1) as recycle:
            self.window.remove_files("input", clear=True)
            recycle.assert_called_once_with(self.base / "input", [self.base / "input" / "甲部门 考勤.xlsx"])

    def test_empty_input_or_invalid_config_stops_before_worker_and_running_locks_changes(self):
        with patch.object(gui.messagebox, "showinfo") as info, patch.object(gui, "Thread") as worker:
            self.window.start()
            info.assert_called_once()
            worker.assert_not_called()
        (self.base / "config.json").write_text("broken", encoding="utf-8")
        self.assertFalse(self.window.import_files([self.file()]))
        with patch.object(gui, "Thread") as worker:
            self.window.start()
            worker.assert_not_called()
        self.assertFalse(self.window.running)
        self.window.running = True
        with patch.object(gui, "recycle_entries") as recycle, patch.object(gui.filedialog, "askopenfilenames") as select:
            self.window.select_files()
            self.window.remove_files("input", clear=True)
            self.assertFalse(self.window.import_files([self.file()]))
            self.assertEqual(self.window.drop_files(SimpleNamespace(data="")), gui.REFUSE_DROP)
            recycle.assert_not_called()
            select.assert_not_called()
        self.window.running = False

    def test_real_background_batch_populates_output_tree_and_source_files_are_preserved(self):
        sources = []
        for index, name in enumerate(("甲部门.xlsx", "乙部门.xlsm"), 1):
            path = self.source / name
            workbook = valid_workbook()
            workbook["每日统计"].cell(5, 2).value = f"user{index}"
            for row in (4, 5):
                workbook["原始记录"].cell(row, 2).value = f"user{index}"
            workbook.save(path)
            workbook.close()
            sources.append(path)
        before = {path: path.read_bytes() for path in sources}
        self.window.import_files(sources)
        self.window.start()
        deadline = time.monotonic() + 20
        while self.window.running and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.window.running, "后台统计超时")
        self.assertEqual(self.window.counts.get(), "已完成 2/2 个文件")
        self.assertEqual(self.window.result_dir.parent, self.base / "output")
        self.assertEqual(len(self.window.tree_paths["output"]), 3)
        self.assertTrue(self.window.trees["output"].item(self.window.result_dir.name, "open"))
        self.assertEqual(before, {path: path.read_bytes() for path in sources})
        self.errors.assert_not_called()


if __name__ == "__main__":
    unittest.main()
