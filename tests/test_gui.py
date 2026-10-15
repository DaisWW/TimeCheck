from contextlib import redirect_stdout
from datetime import date
import io
from pathlib import Path
from queue import Queue
import tempfile
import unittest
from unittest.mock import patch

from attendance_analyzer import gui
from attendance_analyzer.excel_reader import WorkbookInputError
from attendance_analyzer.models import InputIssue


def drain(events):
    items = []
    while not events.empty():
        items.append(events.get_nowait())
    return items


class GuiJobTest(unittest.TestCase):
    def test_missing_csv_stops_before_analysis_and_saves_readable_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = Queue()
            outer_output = io.StringIO()
            with patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer, redirect_stdout(outer_output):
                gui.run_job(root / "input", root / "output", root / "config.csv", root / "logs", events)
                print("stdout restored")
            analyzer.assert_not_called()
            items = drain(events)
            self.assertEqual(items[-1][0:2], ("finished", 2))
            self.assertIn("config.csv", items[-1][2].read_text(encoding="utf-8-sig"))
            self.assertEqual(outer_output.getvalue(), "stdout restored\n")

    def test_batch_reports_progress_and_stops_on_invalid_file_with_source_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.csv"
            config.write_text("分类,配置项,值,说明\n工作日,每日工作分钟,450,\n", encoding="utf-8-sig")
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

    def test_success_completes_progress_without_writing_to_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.csv"
            config.write_text("分类,配置项,值,说明\n工作日,每日工作分钟,450,\n", encoding="utf-8-sig")
            input_path = root / "考勤.xlsx"
            input_path.touch()
            events = Queue()
            with patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer, patch("attendance_analyzer.cli.OvertimeReportWriter") as writer:
                analyzer.return_value.analyze_overtime.return_value = []
                analyzer.return_value.last_input_issues = []
                gui.run_job(input_path, root / "本次结果", config, root / "logs", events)
                writer.return_value.write.assert_called_once_with(root / "本次结果" / "考勤_加班分析.xlsx", [], [])
            items = drain(events)
            self.assertEqual(items[-1][0:2], ("finished", 0))
            self.assertEqual(items[-1][3:], (1, 1))
            self.assertIn(("progress", 1, 1, "统计完成"), items)

    def test_unexpected_failure_is_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = Queue()
            with patch("attendance_analyzer.gui.cli.main", side_effect=RuntimeError("unexpected detail")):
                gui.run_job(root / "input", root / "output", root / "config.csv", root / "logs", events)
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
                gui.run_job(root / "input", root / "output", root / "config.csv", blocked, events)
            main.assert_not_called()
            items = drain(events)
            self.assertIn("无法保存运行日志", items[0][1])
            self.assertEqual(items[-1][0:2], ("finished", 3))


if __name__ == "__main__":
    unittest.main()
