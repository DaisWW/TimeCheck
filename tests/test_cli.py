from pathlib import Path
import io
import json
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from attendance_analyzer import cli
from attendance_analyzer.config import AppConfig


class CliTest(unittest.TestCase):
    def test_incomplete_excel_archive_reports_an_input_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "损坏的考勤.xlsx", root / "结果.xlsx"
            with ZipFile(source, "w"):
                pass
            with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                code = cli._analyze_file(source, output, AppConfig())
            self.assertEqual(code, 4)
            self.assertIn("请从钉钉重新导出", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertFalse(output.exists())

    def test_batch_rejects_colliding_report_names_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in ("考勤.xlsx", "考勤.xlsm"):
                (root / filename).touch()
            with (
                patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer,
                patch("attendance_analyzer.cli.OvertimeReportWriter") as writer,
                patch("sys.stderr", new_callable=io.StringIO) as stderr,
            ):
                code = cli._analyze_directory(root, root / "output", AppConfig())
            self.assertEqual(code, 2)
            analyzer.assert_not_called()
            writer.assert_not_called()
            for fragment in ("考勤.xlsx", "考勤.xlsm", "重名", "重命名"):
                self.assertIn(fragment, stderr.getvalue())

    def test_no_arguments_analyzes_all_input_files_into_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            input_dir.mkdir()
            first_input = input_dir / "一月考勤.xlsx"
            second_input = input_dir / "二月考勤.xlsm"
            generated_report = input_dir / "旧考勤_加班分析.xlsx"
            for path in (first_input, second_input, generated_report):
                path.write_bytes(b"placeholder")

            with (
                patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer_class,
                patch("attendance_analyzer.cli.OvertimeReportWriter") as writer_class,
                patch("attendance_analyzer.cli.Path.cwd", return_value=root),
                patch("sys.stdout", new_callable=io.StringIO) as stdout,
            ):
                analyzer_class.return_value.analyze_overtime.return_value = []
                analyzer_class.return_value.last_input_issues = []
                code = cli.main([])

            self.assertEqual(code, 0)
            self.assertEqual(
                [call.args[0] for call in analyzer_class.return_value.analyze_overtime.call_args_list],
                [first_input, second_input],
            )
            self.assertEqual(
            [call.args[0] for call in writer_class.return_value.write.call_args_list],
                [
                    root / "output" / "一月考勤_加班分析.xlsx",
                    root / "output" / "二月考勤_加班分析.xlsx",
                ],
            )
            self.assertIn("批量分析完成：2 个文件", stdout.getvalue())

    def test_explicit_input_defaults_to_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "考勤.xlsx"
            input_path.write_bytes(b"placeholder")

            with (
                patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer_class,
                patch("attendance_analyzer.cli.OvertimeReportWriter") as writer_class,
                patch("attendance_analyzer.cli.Path.cwd", return_value=root),
            ):
                analyzer_class.return_value.analyze_overtime.return_value = []
                analyzer_class.return_value.last_input_issues = []
                code = cli.main([str(input_path)])

            self.assertEqual(code, 0)
            writer_class.return_value.write.assert_called_once_with(
                root / "output" / "考勤_加班分析.xlsx", [], []
            )

    def test_permission_error_returns_friendly_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "考勤.xlsx"
            output_path = Path(temp_dir) / "结果.xlsx"
            input_path.write_bytes(b"placeholder")

            with (
                patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer_class,
                patch("attendance_analyzer.cli.OvertimeReportWriter") as writer_class,
                patch("sys.stderr", new_callable=io.StringIO) as stderr,
            ):
                analyzer_class.return_value.analyze_overtime.return_value = []
                analyzer_class.return_value.last_input_issues = []
                writer_class.return_value.write.side_effect = PermissionError()

                code = cli.main([str(input_path), "-o", str(output_path)])

            self.assertEqual(code, 3)
            self.assertIn("无法写入输出文件", stderr.getvalue())

    def test_invalid_input_returns_failure_without_writing_a_report(self):
        with (
            patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer_class,
            patch("attendance_analyzer.cli.OvertimeReportWriter") as writer_class,
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            analyzer_class.return_value.analyze_overtime.side_effect = ValueError("每日统计 第 5 行：未知格式")
            code = cli._analyze_file(Path("input.xlsx"), Path("result.xlsx"), AppConfig())
        self.assertEqual(code, 4)
        writer_class.assert_not_called()
        self.assertIn("每日统计 第 5 行", stderr.getvalue())

    def test_disk_error_returns_friendly_failure(self):
        with (
            patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer_class,
            patch("attendance_analyzer.cli.OvertimeReportWriter") as writer_class,
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            analyzer_class.return_value.analyze_overtime.return_value = []
            analyzer_class.return_value.last_input_issues = []
            writer_class.return_value.write.side_effect = OSError("disk full")
            code = cli._analyze_file(Path("input.xlsx"), Path("result.xlsx"), AppConfig())
        self.assertEqual(code, 3)
        self.assertIn("disk full", stderr.getvalue())
        self.assertIn("剩余空间", stderr.getvalue())

    def test_default_json_reaches_analyzer_and_reports_selected_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "考勤.xlsx"
            input_path.write_bytes(b"placeholder")
            config_path = root / "config.json"
            defaults = AppConfig()
            replace(defaults, workday=replace(defaults.workday, work_minutes=420)).save(config_path)
            with (
                patch("attendance_analyzer.cli.Path.cwd", return_value=root),
                patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer_class,
                patch("attendance_analyzer.cli.OvertimeReportWriter"),
                patch("sys.stdout", new_callable=io.StringIO) as stdout,
            ):
                analyzer_class.return_value.analyze_overtime.return_value = []
                analyzer_class.return_value.last_input_issues = []
                code = cli.main([str(input_path)])
            self.assertEqual(code, 0)
            self.assertEqual(analyzer_class.call_args.args[0].workday.work_minutes, 420)
            self.assertIn(str(config_path), stdout.getvalue())

    def test_invalid_json_stops_analysis(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = AppConfig().to_dict()
            data["workday"]["work_minutes"] = 7.5
            (root / "config.json").write_text(json.dumps(data), encoding="utf-8")
            with (
                patch("attendance_analyzer.cli.Path.cwd", return_value=root),
                patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer_class,
                patch("attendance_analyzer.cli.OvertimeReportWriter") as writer_class,
                patch("sys.stderr", new_callable=io.StringIO) as stderr,
            ):
                code = cli.main([])
            self.assertEqual(code, 2)
            analyzer_class.assert_not_called()
            writer_class.assert_not_called()
            for fragment in ("config.json", "workday.work_minutes", "7.5"):
                self.assertIn(fragment, stderr.getvalue())

    def test_explicit_toml_is_rejected_before_analysis(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text("[workday]\nwork_minutes=450", encoding="utf-8")
            with (
                patch("attendance_analyzer.cli.AttendanceAnalyzer") as analyzer_class,
                patch("attendance_analyzer.cli.OvertimeReportWriter") as writer_class,
                patch("sys.stderr", new_callable=io.StringIO) as stderr,
            ):
                code = cli.main(["-c", str(config_path)])
            self.assertEqual(code, 2)
            analyzer_class.assert_not_called()
            writer_class.assert_not_called()
            self.assertIn(str(config_path), stderr.getvalue())
            self.assertIn("仅支持 .json", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
