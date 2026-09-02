from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .config import AppConfig
from .models import InputIssue, OvertimeResult
from .time_utils import fmt_dt, fmt_time


class OvertimeReportWriter:
    def __init__(self, config: AppConfig):
        self._config = config

    def write(
        self,
        output_path: Path,
        results: list[OvertimeResult],
        input_issues: list[InputIssue] | None = None,
    ) -> None:
        input_issues = input_issues or []
        workbook = Workbook()
        detail_sheet = workbook.active
        detail_sheet.title = "加班明细"

        self._write_detail(detail_sheet, results)
        self._write_employee_summary(workbook.create_sheet("员工汇总"), results, input_issues)
        self._write_daily_summary(workbook.create_sheet("每日汇总"), results, input_issues)
        self._write_supplement_punch_notifications(workbook.create_sheet("待补卡通知"), results)
        self._write_approval_reviews(workbook.create_sheet("待审核清单"), results)
        self._write_anomalies(workbook.create_sheet("异常数据"), results)
        self._write_input_issues(workbook.create_sheet("待人工确认"), input_issues)
        self._write_config(workbook.create_sheet("配置"), output_path, input_issues)

        for sheet in workbook.worksheets:
            _freeze_and_autofit(sheet)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(output_path)

    def _write_detail(self, sheet, results: list[OvertimeResult]) -> None:
        headers = [
            "姓名",
            "UserId",
            "日期",
            "班次",
            "加班类型",
            "上班打卡",
            "下班打卡",
            "弹性下班",
            "午休扣除(分钟)",
            "晚餐扣除(分钟)",
            "加班开始",
            "加班结束",
            "原始加班分钟",
            "计入加班分钟",
            "计入加班小时",
            "关联审批单",
            "请假审批覆盖当日分钟(非计薪)",
            "审批加班分钟",
            "审批差异(分钟)",
            "审批核对状态",
            "原始迟到分钟",
            "请假抵扣迟到分钟",
            "迟到分钟",
            "迟到次数(月内)",
            "宽限迟到分钟",
            "扣款迟到分钟",
            "迟到状态",
            "统计状态",
            "备注",
            "计算说明",
        ]
        sheet.append(headers)

        for result in sorted(results, key=lambda item: (item.work_date, item.employee_name)):
            sheet.append(
                [
                    result.employee_name,
                    result.user_id,
                    result.work_date,
                    result.shift,
                    result.overtime_type,
                    fmt_dt(result.first_clock_in),
                    fmt_dt(result.last_clock_out),
                    fmt_time(result.standard_end),
                    result.lunch_break_minutes,
                    result.dinner_break_minutes,
                    fmt_time(result.overtime_start),
                    fmt_time(result.overtime_end),
                    result.raw_overtime_minutes,
                    result.overtime_minutes,
                    result.overtime_hours,
                    result.approval_text,
                    result.approved_leave_minutes,
                    result.approval_overtime_minutes,
                    result.approval_difference_minutes,
                    _approval_status(result),
                    result.late_raw_minutes,
                    result.late_leave_exempt_minutes,
                    result.late_minutes,
                    result.late_occurrence_number,
                    result.late_grace_minutes,
                    result.late_chargeable_minutes,
                    result.late_status,
                    _calculation_status(result, self._config),
                    "；".join(result.notes),
                    _calculation_text(result),
                ]
            )

        _style_header(sheet)
        for row in sheet.iter_rows(min_row=2, min_col=3, max_col=3):
            row[0].number_format = "yyyy-mm-dd"

    def _write_employee_summary(
        self, sheet, results: list[OvertimeResult], input_issues: list[InputIssue]
    ) -> None:
        grouped: dict[tuple[str, str], list[OvertimeResult]] = defaultdict(list)
        for result in results:
            grouped[(result.employee_name, result.user_id)].append(result)
        input_issue_counts = _input_issue_counts_by_employee(input_issues)

        sheet.append(
            [
                "姓名",
                "UserId",
                "统计状态",
                "阻断输入条数",
                "待补卡天数",
                "待审核天数",
                "暂计加班天数",
                "暂计加班分钟",
                "暂计加班小时",
                "迟到次数",
                "迟到分钟",
                "请假抵扣迟到分钟",
                "宽限迟到分钟",
                "扣款迟到分钟",
            ]
        )
        for (name, user_id), items in sorted(grouped.items()):
            minutes = sum(item.overtime_minutes for item in items)
            input_issue_count = input_issue_counts[(name, user_id)]
            if user_id:
                input_issue_count += input_issue_counts[(name, "")]
            supplement_count = sum(
                _supplement_punch_issue(item, self._config) is not None for item in items
            )
            review_count = sum(_needs_review(item) for item in items)
            sheet.append(
                [
                    name,
                    user_id,
                    _summary_status(input_issue_count, supplement_count, review_count),
                    input_issue_count,
                    supplement_count,
                    review_count,
                    sum(1 for item in items if item.has_overtime),
                    minutes,
                    round(minutes / 60, 2),
                    sum(1 for item in items if item.late_minutes > 0),
                    sum(item.late_minutes for item in items),
                    sum(item.late_leave_exempt_minutes for item in items),
                    sum(item.late_grace_minutes for item in items),
                    sum(item.late_chargeable_minutes for item in items),
                ]
            )
        _style_header(sheet)

    def _write_daily_summary(
        self, sheet, results: list[OvertimeResult], input_issues: list[InputIssue]
    ) -> None:
        grouped: dict[date, list[OvertimeResult]] = defaultdict(list)
        for result in results:
            grouped[result.work_date].append(result)
        input_issue_counts = _input_issue_counts_by_date(input_issues)

        sheet.append(
            [
                "日期",
                "统计状态",
                "阻断输入条数",
                "待补卡人数",
                "待审核人数",
                "加班人数",
                "暂计加班分钟",
                "暂计加班小时",
                "迟到人数",
                "迟到分钟",
                "请假抵扣迟到分钟",
                "宽限迟到分钟",
                "扣款迟到分钟",
            ]
        )
        for work_date, items in sorted(grouped.items()):
            minutes = sum(item.overtime_minutes for item in items)
            input_issue_count = input_issue_counts[work_date]
            supplement_count = sum(
                _supplement_punch_issue(item, self._config) is not None for item in items
            )
            review_count = sum(_needs_review(item) for item in items)
            sheet.append(
                [
                    work_date,
                    _summary_status(input_issue_count, supplement_count, review_count),
                    input_issue_count,
                    supplement_count,
                    review_count,
                    sum(1 for item in items if item.has_overtime),
                    minutes,
                    round(minutes / 60, 2),
                    sum(1 for item in items if item.late_minutes > 0),
                    sum(item.late_minutes for item in items),
                    sum(item.late_leave_exempt_minutes for item in items),
                    sum(item.late_grace_minutes for item in items),
                    sum(item.late_chargeable_minutes for item in items),
                ]
            )
        _style_header(sheet)
        for row in sheet.iter_rows(min_row=2, min_col=1, max_col=1):
            row[0].number_format = "yyyy-mm-dd"

    def _write_supplement_punch_notifications(self, sheet, results: list[OvertimeResult]) -> None:
        sheet.append(["姓名", "UserId", "日期", "班次", "需补打卡", "原因", "关联审批单", "建议通知"])

        notification_count = 0
        for result in sorted(results, key=lambda item: (item.work_date, item.employee_name)):
            issue = _supplement_punch_issue(result, self._config)
            if issue is None:
                continue

            missing_type, reason = issue
            sheet.append(
                [
                    result.employee_name,
                    result.user_id,
                    result.work_date,
                    result.shift,
                    missing_type,
                    reason,
                    result.approval_text,
                    f"请补齐 {result.work_date:%Y-%m-%d} 的{missing_type}；补卡后重新导出考勤并运行本工具。",
                ]
            )
            notification_count += 1

        if notification_count == 0:
            sheet.append(["未发现待补卡记录"])

        _style_header(sheet)
        for row in sheet.iter_rows(min_row=2, min_col=3, max_col=3):
            row[0].number_format = "yyyy-mm-dd"

    def _write_approval_reviews(self, sheet, results: list[OvertimeResult]) -> None:
        sheet.append(
            [
                "姓名",
                "UserId",
                "日期",
                "加班类型",
                "审核类型",
                "实际首个打卡",
                "实际最后打卡",
                "暂计加班分钟",
                "请假审批分钟",
                "审批加班分钟",
                "差异(分钟)",
                "容差(分钟)",
                "关联审批单",
                "审核原因",
                "需通知HR",
            ]
        )

        review_count = 0
        for result in sorted(results, key=lambda item: (item.work_date, item.employee_name)):
            if not _needs_review(result):
                continue
            sheet.append(
                [
                    result.employee_name,
                    result.user_id,
                    result.work_date,
                    result.overtime_type,
                    _review_type(result),
                    fmt_dt(result.first_clock_in),
                    fmt_dt(result.last_clock_out),
                    result.overtime_minutes,
                    result.approved_leave_minutes,
                    result.approval_overtime_minutes,
                    result.approval_difference_minutes,
                    self._config.rest_day.approval_difference_tolerance_minutes,
                    result.approval_text,
                    _review_reason(result),
                    "是",
                ]
            )
            review_count += 1

        if review_count == 0:
            sheet.append(["未发现待审核记录"])

        _style_header(sheet)
        for row in sheet.iter_rows(min_row=2, min_col=3, max_col=3):
            row[0].number_format = "yyyy-mm-dd"

    def _write_anomalies(self, sheet, results: list[OvertimeResult]) -> None:
        sheet.append(
            [
                "异常类型",
                "姓名",
                "UserId",
                "日期",
                "班次",
                "上班打卡结果",
                "下班打卡结果",
                "原始记录数",
                "有效原始记录数",
                "使用每日统计兜底",
                "关联审批单",
                "说明",
            ]
        )

        anomaly_count = 0
        for result in sorted(results, key=lambda item: (item.work_date, item.employee_name)):
            for anomaly_type, message in _anomalies_for(result, self._config):
                sheet.append(
                    [
                        anomaly_type,
                        result.employee_name,
                        result.user_id,
                        result.work_date,
                        result.shift,
                        result.start_result,
                        result.end_result,
                        result.raw_punch_count,
                        result.valid_raw_punch_count,
                        "是" if result.used_daily_fallback else "",
                        result.approval_text,
                        message,
                    ]
                )
                anomaly_count += 1

        if anomaly_count == 0:
            sheet.append(["未发现异常数据"])

        _style_header(sheet)
        for row in sheet.iter_rows(min_row=2, min_col=4, max_col=4):
            row[0].number_format = "yyyy-mm-dd"

    def _write_input_issues(self, sheet, input_issues: list[InputIssue]) -> None:
        sheet.append(
            [
                "来源工作表",
                "源行号",
                "姓名",
                "UserId",
                "考勤日期",
                "字段",
                "原始值",
                "问题",
                "对统计的影响",
                "建议处理",
                "问题类型",
                "阻断自动汇总",
                "HR选择",
                "HR处理说明",
            ]
        )

        for issue in input_issues:
            sheet.append(
                [
                    issue.source_sheet,
                    issue.source_row,
                    issue.employee_name,
                    issue.user_id,
                    issue.work_date,
                    issue.field_name,
                    issue.raw_value,
                    issue.message,
                    issue.impact,
                    issue.suggested_action,
                    issue.issue_type,
                    "是" if issue.blocks_summary else "否",
                    "",
                    "",
                ]
            )

        if not input_issues:
            sheet.append(["未发现需要人工确认的输入数据"])
        else:
            choice_validation = DataValidation(
                type="list",
                formula1='"更正源数据后重跑,确认排除本行,人工核定后重跑"',
                allow_blank=True,
            )
            sheet.add_data_validation(choice_validation)
            choice_validation.add(f"M2:M{len(input_issues) + 1}")

        _style_header(sheet)
        sheet.auto_filter.ref = f"A1:N{sheet.max_row}"
        for row in sheet.iter_rows(min_row=2, min_col=7, max_col=14):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for row in sheet.iter_rows(min_row=2, min_col=5, max_col=5):
            row[0].number_format = "yyyy-mm-dd"

    def _write_config(
        self, sheet, output_path: Path, input_issues: list[InputIssue]
    ) -> None:
        workday = self._config.workday
        overtime = self._config.overtime
        rest_day = self._config.rest_day
        rows = [
            ("输出文件", str(output_path)),
            ("输入待人工确认总条数", len(input_issues)),
            ("其中阻断自动汇总条数", sum(issue.blocks_summary for issue in input_issues)),
            ("待人工确认处理", "不参与自动猜测；在待人工确认填写 HR 选择后修正源数据并重跑"),
            ("待人工确认 HR选择", "更正源数据后重跑 / 确认排除本行 / 人工核定后重跑"),
            ("弹性上班开始", workday.flex_start_begin.strftime("%H:%M")),
            ("弹性上班结束", workday.flex_start_end.strftime("%H:%M")),
            ("10:00后下班规则", "固定 19:00，不因迟到顺延"),
            ("工作分钟", workday.work_minutes),
            ("午休分钟", workday.lunch_break_minutes),
            ("迟到次数宽限上限(月)", workday.late_grace_occurrences_per_month),
            ("迟到分钟宽限上限(月)", workday.late_grace_minutes_per_month),
            ("是否统计非工作日加班", str(workday.count_non_workday_overtime)),
            ("休息班次关键字", "、".join(workday.rest_shift_keywords)),
            ("缺卡处理", "缺少成对有效打卡时不计入；员工汇总标为待补卡"),
            ("请假处理", "请假等审批区间抵扣对应迟到；同日有加班时暂计并交 HR 审核"),
            (
                "非工作日固定午休",
                f"{rest_day.fixed_lunch_start:%H:%M}-"
                f"{(datetime.combine(date.min, rest_day.fixed_lunch_start) + timedelta(minutes=rest_day.fixed_lunch_minutes)):%H:%M}",
            ),
            ("审批与实际差异容差(分钟)", rest_day.approval_difference_tolerance_minutes),
            ("非工作日晚餐时段起点", rest_day.dinner_break_start.strftime("%H:%M")),
            ("非工作日晚餐扣除分钟", rest_day.dinner_break_minutes),
            ("额外节假日日期", "、".join(item.isoformat() for item in rest_day.holiday_dates)),
            ("节假日识别", "配置日期，或工作日日期但班次包含休息关键字"),
            ("工作日晚餐扣除分钟", overtime.dinner_break_minutes),
            ("最小加班分钟", overtime.min_overtime_minutes),
            ("取整单位分钟", overtime.rounding_unit_minutes),
            ("取整方式", overtime.rounding_mode),
            ("无效打卡前缀", "、".join(overtime.invalid_punch_prefixes)),
        ]
        sheet.append(["配置项", "值"])
        for row in rows:
            sheet.append(list(row))
        _style_header(sheet)


def _style_header(sheet) -> None:
    fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.fill = fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 30


def _freeze_and_autofit(sheet) -> None:
    sheet.freeze_panes = "A2"
    for column_cells in sheet.columns:
        max_length = 0
        column_letter = get_column_letter(column_cells[0].column)
        for cell in column_cells:
            value = cell.value
            if value is None:
                continue
            max_length = max(max_length, len(str(value)))
        sheet.column_dimensions[column_letter].width = min(max(max_length + 2, 10), 40)

    if sheet.title == "待人工确认":
        sheet.column_dimensions["M"].width = 24
        sheet.column_dimensions["N"].width = 32
        for row in range(2, sheet.max_row + 1):
            sheet.row_dimensions[row].height = 48

    for cell in sheet[1]:
        if cell.value == "UserId":
            for row in range(2, sheet.max_row + 1):
                sheet.cell(row, cell.column).number_format = "@"


def _calculation_text(result: OvertimeResult) -> str:
    if (
        result.overtime_type != "工作日"
        and result.first_clock_in is not None
        and result.last_clock_out is not None
    ):
        deduction_text = f"固定午休扣除 {result.lunch_break_minutes} 分钟"
        if result.dinner_break_minutes:
            deduction_text += f"，晚餐扣除 {result.dinner_break_minutes} 分钟"
        return (
            f"{result.overtime_type}首个有效打卡 {fmt_time(result.first_clock_in)}，"
            f"最后有效打卡 {fmt_time(result.last_clock_out)}；{deduction_text}；"
            f"计入 {result.overtime_minutes} 分钟{_leave_calculation_text(result)}。"
        )

    if result.first_clock_in is None or result.last_clock_out is None or result.standard_end is None:
        notes = "；".join(result.notes) or "缺少有效上下班打卡"
        return f"未计算：{notes}{_leave_calculation_text(result)}。"

    late_text = _leave_calculation_text(result)
    if result.late_minutes:
        late_text = (
            f"；迟到 {result.late_minutes} 分钟，宽限 {result.late_grace_minutes} 分钟，"
            f"扣款 {result.late_chargeable_minutes} 分钟"
            f"{_leave_calculation_text(result)}"
        )
    return (
        f"首个有效打卡 {fmt_time(result.first_clock_in)}，弹性下班 {fmt_time(result.standard_end)}；"
        f"晚餐扣除 {result.dinner_break_minutes} 分钟后 {fmt_time(result.overtime_start)} 开始算加班；"
        f"最后有效打卡 {fmt_time(result.last_clock_out)}；"
        f"计入 {result.overtime_minutes} 分钟{late_text}。"
    )


def _leave_calculation_text(result: OvertimeResult) -> str:
    if not result.approved_leave_minutes:
        return ""
    text = f"；请假/授权审批区间覆盖当日 {result.approved_leave_minutes} 分钟（非计薪时长）"
    if result.late_leave_exempt_minutes:
        text += f"，抵扣迟到 {result.late_leave_exempt_minutes} 分钟"
    return text


def _anomalies_for(result: OvertimeResult, config: AppConfig) -> list[tuple[str, str]]:
    anomalies: list[tuple[str, str]] = []
    start_missing = not result.start_time_text and bool(result.end_time_text)
    end_missing = bool(result.start_time_text) and not result.end_time_text
    approved_non_punch = _has_approved_non_punch_reason(result)
    supplement_issue = _supplement_punch_issue(result, config)

    if supplement_issue is not None:
        _, message = supplement_issue
        anomalies.append(("待补卡", message))

    if start_missing and not approved_non_punch:
        anomalies.append(("有下班无上班", "每日统计中缺少上班打卡时间，但存在下班打卡时间。"))
    if end_missing and not approved_non_punch:
        anomalies.append(("有上班无下班", "每日统计中存在上班打卡时间，但缺少下班打卡时间。"))
    if ("缺卡" in result.start_result or "缺卡" in result.end_result) and result.raw_punch_count > 0:
        anomalies.append(("缺卡但有原始记录", "每日统计显示缺卡，但原始记录中存在该员工当天打卡流水。"))
    if result.raw_punch_count > 0 and result.valid_raw_punch_count == 0:
        anomalies.append(("原始记录全部无效", "原始记录存在打卡流水，但都被无效打卡规则排除。"))
    if result.approval_review_reason:
        anomalies.append(("审批与实际打卡待审核", result.approval_review_reason))
    if result.leave_review_reason:
        anomalies.append(("请假与加班待审核", result.leave_review_reason))
    if _is_non_workday(result, config) and result.raw_punch_count > 0 and not config.workday.count_non_workday_overtime:
        anomalies.append(("非工作日有打卡但未统计", "当天为非工作日，配置为不统计非工作日加班。"))
    has_any_attendance_signal = bool(result.start_time_text or result.end_time_text or result.raw_punch_count)
    should_expect_punch_pair = bool(result.shift.strip()) and not _is_rest_day(result, config)
    if (
        any(note == "有效上下班打卡不足" for note in result.notes)
        and (has_any_attendance_signal or should_expect_punch_pair)
        and not approved_non_punch
        and supplement_issue is None
    ):
        anomalies.append(("有效上下班打卡不足", "无法从原始记录或每日统计中得到成对的有效上下班打卡。"))

    return anomalies


def _has_approved_non_punch_reason(result: OvertimeResult) -> bool:
    if not result.approval_text:
        return False

    approval_keywords = ("请假", "年假", "事假", "病假", "婚假", "调休", "外出", "出差")
    result_keywords = ("请假", "年假", "事假", "病假", "婚假", "调休", "外出", "出差")
    has_approval = any(keyword in result.approval_text for keyword in approval_keywords)
    has_non_punch_result = any(keyword in result.start_result or keyword in result.end_result for keyword in result_keywords)
    return has_approval and has_non_punch_result


def _supplement_punch_issue(result: OvertimeResult, config: AppConfig) -> tuple[str, str] | None:
    if result.punch_pair_complete or _has_approved_non_punch_reason(result):
        return None

    if not _requires_punch_pair(result, config):
        return None

    if not result.start_time_text and result.end_time_text:
        missing_type = "上班打卡"
    elif result.start_time_text and not result.end_time_text:
        missing_type = "下班打卡"
    else:
        missing_type = "上班和下班打卡"

    return missing_type, "缺少成对有效打卡，当天加班不计入，补卡后需重新统计。"


def _requires_punch_pair(result: OvertimeResult, config: AppConfig) -> bool:
    if not result.shift.strip():
        return False
    if not _is_rest_day(result, config):
        return True
    return bool(
        result.raw_punch_count
        or result.start_time_text
        or result.end_time_text
        or "加班" in result.approval_text
    )


def _calculation_status(result: OvertimeResult, config: AppConfig) -> str:
    if _supplement_punch_issue(result, config) is not None:
        return "待补卡，不计入"
    if _needs_review(result):
        return "待审核，暂计"
    if _is_non_workday(result, config) and not config.workday.count_non_workday_overtime:
        return "休息日未统计"
    if result.first_clock_in is None or result.last_clock_out is None:
        return "未计算"
    return "有效"


def _approval_status(result: OvertimeResult) -> str:
    if _needs_review(result):
        return "待审核"
    if result.approval_overtime_minutes is not None:
        return "已核对"
    return ""


def _summary_status(input_issue_count: int, supplement_count: int, review_count: int) -> str:
    if input_issue_count:
        return "待人工确认，汇总未生效"
    if supplement_count:
        return "待补卡，汇总未生效"
    if review_count:
        return "待审核，汇总未生效"
    return "有效"


def _needs_review(result: OvertimeResult) -> bool:
    return bool(result.approval_review_reason or result.leave_review_reason)


def _review_type(result: OvertimeResult) -> str:
    types: list[str] = []
    if result.approval_review_reason:
        types.append("加班审批差异")
    if result.leave_review_reason:
        types.append("请假与加班并存")
    return "、".join(types)


def _review_reason(result: OvertimeResult) -> str:
    return "；".join(
        reason for reason in (result.approval_review_reason, result.leave_review_reason) if reason
    )


def _input_issue_counts_by_employee(
    input_issues: list[InputIssue],
) -> defaultdict[tuple[str, str], int]:
    counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    for issue in input_issues:
        if issue.employee_name and issue.blocks_summary:
            counts[(issue.employee_name, issue.user_id)] += 1
    return counts


def _input_issue_counts_by_date(input_issues: list[InputIssue]) -> defaultdict[date, int]:
    counts: defaultdict[date, int] = defaultdict(int)
    for issue in input_issues:
        if issue.work_date is not None and issue.blocks_summary:
            counts[issue.work_date] += 1
    return counts


def _is_rest_day(result: OvertimeResult, config: AppConfig) -> bool:
    return any(keyword in result.shift for keyword in config.workday.rest_shift_keywords)


def _is_non_workday(result: OvertimeResult, config: AppConfig) -> bool:
    return (
        result.overtime_type != "工作日"
        or _is_rest_day(result, config)
        or result.work_date in config.rest_day.holiday_dates
    )
