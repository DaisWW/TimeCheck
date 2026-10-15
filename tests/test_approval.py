from datetime import datetime
import unittest

from attendance_analyzer.approval import (
    approval_parse_issues, parse_leave_approval_intervals, parse_overtime_approval_intervals,
)


class ApprovalParserTest(unittest.TestCase):
    def test_leave_intervals_are_parsed_without_treating_supplement_punch_as_leave(self):
        intervals = parse_leave_approval_intervals(
            "事假07-01 10:00到07-01 12:00 2小时，补卡申请07-01 19:01到07-01 19:01",
            2026,
        )

        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0].kind, "事假")
        self.assertEqual(intervals[0].start, datetime(2026, 7, 1, 10, 0))
        self.assertEqual(intervals[0].end, datetime(2026, 7, 1, 12, 0))

    def test_invalid_leave_time_is_reported_without_raising(self):
        issues = approval_parse_issues("病假07-01 25:00到07-01 12:00", 2026)

        self.assertEqual(len(issues), 1)
        self.assertIn("日期或时间无效", issues[0].message)

    def test_unknown_partial_or_reversed_approvals_are_blocking(self):
        for text in (
            "产假08-03 09:30到08-03 18:30 1天",
            "事假08-03 15:00到08-03 10:00",
            "年假12-31 09:30到01-01 18:30",
            "年假08-03 10:00到08-03 11:00:99",
            "年假08-03 10:00到08-03 11:00 未知备注",
            "补卡申请日期坏了",
            "未识别审批",
        ):
            with self.subTest(text=text):
                self.assertTrue(any(issue.blocks_summary for issue in approval_parse_issues(text, 2026)))
                self.assertEqual(parse_leave_approval_intervals(text, 2026), [])

    def test_explicit_overnight_and_cross_year_dates_are_preserved(self):
        intervals = parse_overtime_approval_intervals(
            "加班2026-12-31 22:00到2027-01-01 01:00 3小时", 2026
        )
        self.assertEqual(intervals[0].start, datetime(2026, 12, 31, 22))
        self.assertEqual(intervals[0].end, datetime(2027, 1, 1, 1))

    def test_same_time_supplement_is_advisory_but_same_time_leave_is_error(self):
        self.assertFalse(approval_parse_issues("补卡申请08-03 19:00到08-03 19:00", 2026)[0].blocks_summary)
        self.assertTrue(approval_parse_issues("事假08-03 19:00到08-03 19:00", 2026)[0].blocks_summary)


if __name__ == "__main__":
    unittest.main()
