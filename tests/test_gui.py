from contextlib import redirect_stdout
from datetime import date
import io
from pathlib import Path
from queue import Queue
import tempfile
import unittest
from unittest.mock import call, patch

from attendance_analyzer import job_runner as gui
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
    def test_single_workbook_reports_processing_stages_before_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            AppConfig().save(root / "config.json")
            source = root / "考勤.xlsx"
            workbook = valid_workbook()
            workbook.save(source)
            workbook.close()
            events = Queue()
            gui.run_job(source, root / "output", root / "config.json", root / "logs", events)
            items = drain(events)
            stages = [item for item in items if item[0] == "stage"]
            self.assertEqual(list(dict.fromkeys(item[1] for item in stages)),
                             ["read", "calculate", "report", "save"])
            self.assertTrue(all("考勤.xlsx" in item[2] for item in stages))
            self.assertLess(items.index(stages[-1]), next(index for index, item in enumerate(items)
                                                       if item[:3] == ("progress", 1, 1)))
            self.assertEqual(items[-1][0:2], ("finished", 0))

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
            with patch("attendance_analyzer.job_runner.cli.main", side_effect=RuntimeError("unexpected detail")):
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
            with patch("attendance_analyzer.job_runner.cli.main") as main:
                gui.run_job(root / "input", root / "output", root / "config.json", blocked, events)
            main.assert_not_called()
            items = drain(events)
            self.assertIn("无法保存运行日志", items[0][1])
            self.assertEqual(items[-1][0:2], ("finished", 3))
