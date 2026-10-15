import argparse
from contextlib import redirect_stdout
from datetime import date
import io
import json
from pathlib import Path
from queue import Queue
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from attendance_analyzer import gui
from attendance_analyzer.config import AppConfig
from attendance_analyzer.excel_reader import WorkbookInputError
from attendance_analyzer.models import InputIssue


class GuiPathsTest(unittest.TestCase):
    def args(self, **overrides):
        return argparse.Namespace(**({"input": None, "output": None, "config": None} | overrides))

    def test_first_start_uses_adjacent_defaults_without_creating_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths, warning = gui.load_window_paths(root, self.args())
            self.assertEqual(paths, {"input": str(root / "input"), "output": str(root / "output"), "config": str(root / "config.json")})
            self.assertFalse(warning)
            self.assertFalse((root / "window_paths.json").exists())

    def test_round_trip_restores_unicode_paths_including_missing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = {"input": str(root / "每月考勤"), "output": str(root / "统计 结果"), "config": str(root / "自选规则/规则.json")}
            gui.save_window_paths(root, selected)
            paths, warning = gui.load_window_paths(root, self.args())
            self.assertEqual(paths, selected)
            self.assertFalse(warning)
            self.assertFalse(Path(paths["config"]).exists())

    def test_explicit_arguments_override_saved_paths_individually(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = {key: str(root / f"saved_{key}") for key in ("input", "output", "config")}
            gui.save_window_paths(root, selected)
            for key in selected:
                with self.subTest(key=key):
                    override = root / f"参数路径_{key}"
                    paths, warning = gui.load_window_paths(root, self.args(**{key: str(override)}))
                    self.assertEqual(paths, selected | {key: str(override)})
                    self.assertFalse(warning)

    def test_corrupt_or_invalid_settings_show_warning_before_using_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = ["{broken", "[]", '{"input":3}', json.dumps({key: "relative" for key in ("input", "output", "config")})]
            for value in values:
                with self.subTest(value=value):
                    (root / "window_paths.json").write_text(value, encoding="utf-8")
                    paths, warning = gui.load_window_paths(root, self.args())
                    self.assertEqual(paths["config"], str(root / "config.json"))
                    self.assertIn("window_paths.json", warning)
                    self.assertIn("请检查并重新选择", warning)
                    self.assertEqual((root / "window_paths.json").read_text(encoding="utf-8"), value)

    def test_failed_atomic_save_preserves_previous_paths_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {key: str(root / key) for key in ("input", "output", "config")}
            gui.save_window_paths(root, original)
            with patch("attendance_analyzer.gui.Path.replace", side_effect=PermissionError("blocked")):
                with self.assertRaises(PermissionError):
                    gui.save_window_paths(root, original | {"input": str(root / "changed")})
            self.assertEqual(json.loads((root / "window_paths.json").read_text(encoding="utf-8")), original)
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_missing_restored_config_is_reported_instead_of_using_adjacent_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            AppConfig().save(root / "config.json")
            selected = {"input": str(root / "input"), "output": str(root / "output"), "config": str(root / "已移走/config.json")}
            gui.save_window_paths(root, selected)
            paths, _ = gui.load_window_paths(root, self.args())
            events = Queue()
            with patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer:
                gui.run_job(Path(paths["input"]), Path(paths["output"]), Path(paths["config"]), root / "logs", events)
            analyzer.assert_not_called()
            items = drain(events)
            self.assertEqual(items[-1][:2], ("finished", 2))
            self.assertIn(selected["config"], items[-1][2].read_text(encoding="utf-8-sig"))


def drain(events):
    items = []
    while not events.empty():
        items.append(events.get_nowait())
    return items


class GuiDirectoryTest(unittest.TestCase):
    def test_start_rejects_file_or_missing_input_and_file_output_before_starting_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "每月考勤"
            input_dir.mkdir()
            file_path = root / "考勤.xlsx"
            file_path.touch()
            for input_path, output_path, title, invalid_path in (
                (file_path, root / "结果", "请选择考勤目录", file_path),
                (root / "已移走", root / "结果", "请选择考勤目录", root / "已移走"),
                (input_dir, file_path, "请选择输出目录", file_path),
            ):
                with self.subTest(input_path=input_path, output_path=output_path):
                    window = Mock(running=False)
                    window.input_value.get.return_value = str(input_path)
                    window.output_value.get.return_value = str(output_path)
                    with patch.object(gui.messagebox, "showerror") as error, patch.object(gui, "Thread") as worker:
                        gui.AttendanceWindow.start(window)
                    worker.assert_not_called()
                    window.remember_paths.assert_not_called()
                    self.assertFalse(window.running)
                    self.assertEqual(error.call_args.args[0], title)
                    self.assertIn(str(invalid_path), error.call_args.args[1])


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


if __name__ == "__main__":
    unittest.main()
