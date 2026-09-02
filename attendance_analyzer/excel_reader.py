from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from .approval import approval_parse_issues
from .config import AppConfig
from .models import AttendanceData, DailyAttendance, InputIssue, PunchRecord
from .time_utils import combine_date_time, parse_date_text, parse_datetime


class DingTalkWorkbookReader:
    def __init__(self, config: AppConfig):
        self._config = config

    def read(self, path: Path) -> AttendanceData:
        workbook = load_workbook(path, data_only=True, read_only=False)
        daily_sheet = workbook[self._config.sheets.daily_sheet]
        raw_sheet = workbook[self._config.sheets.raw_sheet]

        input_issues: list[InputIssue] = []
        daily_records = self._read_daily_records(daily_sheet, input_issues)
        raw_punches = self._read_raw_punches(raw_sheet, input_issues)
        return AttendanceData(
            daily_records=daily_records,
            raw_punches=raw_punches,
            input_issues=input_issues,
        )

    def _read_daily_records(
        self, sheet: Worksheet, input_issues: list[InputIssue] | None = None
    ) -> list[DailyAttendance]:
        # 钉钉“每日统计”是双层表头：第 3 行主标题，第 4 行子标题。
        headers = _headers(sheet, header_rows=(3, 4))
        rows: list[DailyAttendance] = []
        input_issues = input_issues if input_issues is not None else []
        for row in range(5, sheet.max_row + 1):
            name = _cell(sheet, row, headers, "姓名")
            date_text = _cell(sheet, row, headers, "日期")
            work_date = parse_date_text(date_text)
            if not name:
                if _row_has_data(sheet, row):
                    input_issues.append(
                        _input_issue(
                            sheet,
                            row,
                            "",
                            "",
                            None,
                            "姓名",
                            name,
                            "每日统计缺少姓名，无法归属该考勤记录。",
                            "该行不参与统计。",
                            "补全姓名后重新导出并运行。",
                        )
                    )
                continue
            if work_date is None:
                input_issues.append(
                    _input_issue(
                        sheet,
                        row,
                        name,
                        _cell(sheet, row, headers, "UserId"),
                        None,
                        "日期",
                        date_text,
                        "每日统计日期为空或无法解析。",
                        "该行不参与统计。",
                        "修正日期后重新导出并运行。",
                    )
                )
                continue

            user_id = _cell(sheet, row, headers, "UserId")
            start_time_text = _cell(sheet, row, headers, "上班1打卡时间")
            end_time_text = _cell(sheet, row, headers, "下班1打卡时间")
            approval_text = _cell(sheet, row, headers, "关联的审批单")
            for field_name, time_text in (
                ("上班1打卡时间", start_time_text),
                ("下班1打卡时间", end_time_text),
            ):
                if time_text and combine_date_time(work_date, time_text) is None:
                    input_issues.append(
                        _input_issue(
                            sheet,
                            row,
                            name,
                            user_id,
                            work_date,
                            field_name,
                            time_text,
                            "每日统计打卡时间无法解析。",
                            "每日统计打卡兜底不可用；可能导致缺卡或加班不计入。",
                            "修正打卡时间后重新导出并运行。",
                            issue_type="解析问题",
                        )
                    )
            approval_issues = approval_parse_issues(approval_text, work_date.year)
            if approval_issues:
                messages = list(dict.fromkeys(issue.message for issue in approval_issues))
                blocks_summary = any(issue.blocks_summary for issue in approval_issues)
                input_issues.append(
                    _input_issue(
                        sheet,
                        row,
                        name,
                        user_id,
                        work_date,
                        "关联的审批单",
                        approval_text,
                        "；".join(messages),
                        "加班审批核对或请假抵扣无法自动执行。",
                        "在钉钉审批中确认起止时间后重新导出并运行。",
                        issue_type="解析问题" if blocks_summary else "业务待确认",
                        blocks_summary=blocks_summary,
                    )
                )

            rows.append(
                DailyAttendance(
                    employee_name=name,
                    user_id=user_id,
                    work_date=work_date,
                    shift=_cell(sheet, row, headers, "班次"),
                    start_time_text=start_time_text,
                    start_result=_cell(sheet, row, headers, "上班1打卡结果"),
                    end_time_text=end_time_text,
                    end_result=_cell(sheet, row, headers, "下班1打卡结果"),
                    approval_text=approval_text,
                    source_row=row,
                )
            )
        return rows

    def _read_raw_punches(
        self, sheet: Worksheet, input_issues: list[InputIssue] | None = None
    ) -> dict[tuple[str, date], list[PunchRecord]]:
        # 钉钉“原始记录”只有第 3 行是表头，第 4 行开始就是数据。
        headers = _headers(sheet, header_rows=(3,))
        grouped: dict[tuple[str, date], list[PunchRecord]] = defaultdict(list)
        input_issues = input_issues if input_issues is not None else []

        for row in range(4, sheet.max_row + 1):
            name = _cell(sheet, row, headers, "姓名")
            user_id = _cell(sheet, row, headers, "UserId")
            occurred_at_text = _cell(sheet, row, headers, "打卡时间")
            attendance_date_text = _cell(sheet, row, headers, "考勤日期")
            occurred_at = parse_datetime(occurred_at_text)
            attendance_date = parse_date_text(attendance_date_text)
            result = _cell(sheet, row, headers, "打卡结果")
            if not name:
                if _row_has_data(sheet, row):
                    input_issues.append(
                        _input_issue(
                            sheet,
                            row,
                            "",
                            user_id,
                            attendance_date,
                            "姓名",
                            name,
                            "原始记录缺少姓名，无法归属该打卡。",
                            "该打卡不参与统计。",
                            "补全姓名后重新导出并运行。",
                        )
                    )
                continue
            if occurred_at is None:
                input_issues.append(
                    _input_issue(
                        sheet,
                        row,
                        name,
                        user_id,
                        attendance_date,
                        "打卡时间",
                        occurred_at_text,
                        "原始打卡时间为空或无法解析。",
                        "该打卡不参与统计；可能导致缺卡或加班不计入。",
                        "修正打卡时间后重新导出并运行。",
                    )
                )
            if attendance_date is None:
                input_issues.append(
                    _input_issue(
                        sheet,
                        row,
                        name,
                        user_id,
                        None,
                        "考勤日期",
                        attendance_date_text,
                        "原始考勤日期为空或无法解析，无法确认跨午夜归属。",
                        "该打卡不参与统计。",
                        "修正考勤日期后重新导出并运行。",
                        issue_type="解析问题",
                    )
                )
            if occurred_at is None or attendance_date is None:
                continue

            if result.startswith("打卡无效"):
                input_issues.append(
                    _input_issue(
                        sheet,
                        row,
                        name,
                        user_id,
                        attendance_date,
                        "打卡结果",
                        result,
                        "原始记录明确标记为打卡无效，当前不作为有效打卡。",
                        "该行不参与首末有效打卡；可能导致成对打卡不足。",
                        "确认该打卡是否应恢复有效；修正源数据后重新运行。",
                        issue_type="原始数据被规则排除",
                        blocks_summary=False,
                    )
                )

            # 跨午夜的下班卡在“打卡时间”属于次日，但钉钉的“考勤日期”明确归属前一考勤日。
            key = (_person_key(user_id, name), attendance_date)
            grouped[key].append(
                PunchRecord(
                    employee_name=name,
                    user_id=user_id,
                    occurred_at=occurred_at,
                    result=result,
                    source_row=row,
                )
            )

        for punches in grouped.values():
            punches.sort(key=lambda punch: punch.occurred_at)
        return dict(grouped)


def _headers(sheet: Worksheet, header_rows: tuple[int, ...]) -> dict[str, int]:
    result: dict[str, int] = {}
    for column in range(1, sheet.max_column + 1):
        parts: list[str] = []
        for row in header_rows:
            value = _merged_value(sheet, row, column)
            if value is None:
                continue
            text = str(value).strip()
            if text and text not in parts:
                parts.append(text)
        if parts:
            result[" - ".join(parts)] = column
            result.setdefault(parts[-1], column)
    return result


def _merged_value(sheet: Worksheet, row: int, column: int) -> Any:
    value = sheet.cell(row, column).value
    if value is not None:
        return value
    for cell_range in sheet.merged_cells.ranges:
        if cell_range.min_row <= row <= cell_range.max_row and cell_range.min_col <= column <= cell_range.max_col:
            return sheet.cell(cell_range.min_row, cell_range.min_col).value
    return None


def _cell(sheet: Worksheet, row: int, headers: dict[str, int], name: str) -> str:
    column = headers.get(name)
    if column is None:
        return ""
    value = sheet.cell(row, column).value
    if value is None:
        return ""
    return str(value).strip()


def _row_has_data(sheet: Worksheet, row: int) -> bool:
    return any(
        value is not None and str(value).strip()
        for value in (sheet.cell(row, column).value for column in range(1, sheet.max_column + 1))
    )


def _input_issue(
    sheet: Worksheet,
    source_row: int,
    employee_name: str,
    user_id: str,
    work_date: date | None,
    field_name: str,
    raw_value: str,
    message: str,
    impact: str,
    suggested_action: str,
    issue_type: str = "解析问题",
    blocks_summary: bool = True,
) -> InputIssue:
    return InputIssue(
        source_sheet=sheet.title,
        source_row=source_row,
        employee_name=employee_name,
        user_id=user_id,
        work_date=work_date,
        field_name=field_name,
        raw_value=raw_value,
        message=message,
        impact=impact,
        suggested_action=suggested_action,
        issue_type=issue_type,
        blocks_summary=blocks_summary,
    )


def _person_key(user_id: str, name: str) -> str:
    return user_id.strip() or name.strip()


def fallback_punches(day: DailyAttendance) -> list[PunchRecord]:
    first = combine_date_time(day.work_date, day.start_time_text)
    last = combine_date_time(day.work_date, day.end_time_text)
    punches: list[PunchRecord] = []
    if first is not None:
        punches.append(
            PunchRecord(
                employee_name=day.employee_name,
                user_id=day.user_id,
                occurred_at=first,
                result=day.start_result,
                source_row=day.source_row,
            )
        )
    if last is not None and last != first:
        punches.append(
            PunchRecord(
                employee_name=day.employee_name,
                user_id=day.user_id,
                occurred_at=last,
                result=day.end_result,
                source_row=day.source_row,
            )
        )
    punches.sort(key=lambda punch: punch.occurred_at)
    return punches


def person_key(user_id: str, name: str) -> str:
    return _person_key(user_id, name)
