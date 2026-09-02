from pathlib import Path
import io
import tempfile
import unittest
from unittest.mock import patch

from attendance_analyzer import cli


class CliTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
