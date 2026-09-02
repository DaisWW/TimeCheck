from datetime import date
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from attendance_analyzer.config import AppConfig
from attendance_analyzer.models import InputIssue, OvertimeResult
from attendance_analyzer.report_writer import OvertimeReportWriter, _supplement_punch_issue


class OvertimeReportWriterTest(unittest.TestCase):
    def test_sick_leave_does_not_create_missing_punch_notification(self):
        result = OvertimeResult(
            employee_name="Alice",
            user_id="u1",
            work_date=date(2026, 7, 17),
            shift="A 09:30-18:30",
            first_clock_in=None,
            last_clock_out=None,
            standard_end=None,
            dinner_break_minutes=30,
            overtime_start=None,
            overtime_end=None,
            raw_overtime_minutes=0,
            overtime_minutes=0,
            approval_text="病假07-17 08:30到07-17 18:30 7.5小时",
            start_result="请假",
            end_result="请假",
            punch_pair_complete=False,
            notes=["有效上下班打卡不足"],
        )

        self.assertIsNone(_supplement_punch_issue(result, AppConfig()))

    def test_missing_punch_creates_notification_and_invalidates_employee_summary(self):
        result = OvertimeResult(
            employee_name="Alice",
            user_id="u1",
            work_date=date(2026, 5, 6),
            shift="A 09:30-18:30",
            first_clock_in=None,
            last_clock_out=None,
            standard_end=None,
            dinner_break_minutes=30,
            overtime_start=None,
            overtime_end=None,
            raw_overtime_minutes=0,
            overtime_minutes=0,
            approval_text="",
            start_time_text="09:30",
            raw_punch_count=1,
            valid_raw_punch_count=1,
            punch_pair_complete=False,
            notes=["有效上下班打卡不足"],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "result.xlsx"
            OvertimeReportWriter(AppConfig()).write(output_path, [result])
            workbook = load_workbook(output_path, data_only=True)

        notification = workbook["待补卡通知"]
        summary = workbook["员工汇总"]
        self.assertEqual(notification["A2"].value, "Alice")
        self.assertEqual(notification["E2"].value, "下班打卡")
        self.assertEqual(summary["C2"].value, "待补卡，汇总未生效")
        self.assertEqual(summary["E2"].value, 1)

    def test_report_separates_late_minutes_and_removes_overtime_cap_columns(self):
        result = OvertimeResult(
            employee_name="Alice",
            user_id="u1",
            work_date=date(2026, 7, 1),
            shift="A",
            first_clock_in=None,
            last_clock_out=None,
            standard_end=None,
            dinner_break_minutes=30,
            overtime_start=None,
            overtime_end=None,
            raw_overtime_minutes=0,
            overtime_minutes=0,
            approval_text="",
            late_minutes=45,
            late_occurrence_number=1,
            late_grace_minutes=30,
            late_chargeable_minutes=15,
            late_status="第1次迟到：宽限 30 分钟，扣款 15 分钟",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "result.xlsx"
            OvertimeReportWriter(AppConfig()).write(output_path, [result])
            workbook = load_workbook(output_path, data_only=True)

        detail_header = [cell.value for cell in workbook["加班明细"][1]]
        review_header = [cell.value for cell in workbook["待审核清单"][1]]
        self.assertNotIn("是否封顶", detail_header)
        self.assertIn("扣款迟到分钟", detail_header)
        self.assertIn("需通知HR", review_header)

    def test_input_issue_is_written_to_manual_review_and_invalidates_summary(self):
        result = OvertimeResult(
            employee_name="Alice",
            user_id="u1",
            work_date=date(2026, 7, 1),
            shift="A",
            first_clock_in=None,
            last_clock_out=None,
            standard_end=None,
            dinner_break_minutes=30,
            overtime_start=None,
            overtime_end=None,
            raw_overtime_minutes=0,
            overtime_minutes=0,
            approval_text="",
        )
        input_issue = InputIssue(
            source_sheet="原始记录",
            source_row=12,
            employee_name="Alice",
            user_id="u1",
            work_date=date(2026, 7, 1),
            field_name="打卡时间",
            raw_value="25:00",
            message="原始打卡时间为空或无法解析。",
            impact="该打卡不参与统计。",
            suggested_action="修正打卡时间后重新导出并运行。",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "result.xlsx"
            OvertimeReportWriter(AppConfig()).write(output_path, [result], [input_issue])
            workbook = load_workbook(output_path, data_only=True)

        manual_sheet = workbook["待人工确认"]
        summary = workbook["员工汇总"]
        self.assertEqual(manual_sheet["A2"].value, "原始记录")
        self.assertEqual(manual_sheet["G2"].value, "25:00")
        self.assertEqual(manual_sheet.auto_filter.ref, "A1:N2")
        self.assertTrue(manual_sheet["G2"].alignment.wrap_text)
        self.assertEqual(len(manual_sheet.data_validations.dataValidation), 1)
        validation = manual_sheet.data_validations.dataValidation[0]
        self.assertIn("更正源数据后重跑", validation.formula1)
        self.assertEqual(str(validation.sqref), "M2")
        self.assertEqual(summary["C2"].value, "待人工确认，汇总未生效")
        self.assertEqual(summary["D2"].value, 1)


if __name__ == "__main__":
    unittest.main()
