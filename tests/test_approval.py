from datetime import datetime
import unittest

from attendance_analyzer.approval import approval_parse_issues, parse_leave_approval_intervals


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


if __name__ == "__main__":
    unittest.main()
