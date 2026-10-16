from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .approval import approval_parse_issues
from .config import AppConfig
from .excel_reader import DingTalkWorkbookReader, fallback_punches, person_key
from .models import AttendanceData, InputIssue, OvertimeResult
from .rules import OvertimeRule


class AttendanceAnalyzer:
    def __init__(self, config: AppConfig, *, stage: Callable[[str, str], None] | None = None):
        self._config = config
        self._stage = stage
        self._reader = DingTalkWorkbookReader(config)
        self._overtime_rule = OvertimeRule(config)
        self.last_input_issues: list[InputIssue] = []

    def analyze_overtime(self, input_path: Path) -> list[OvertimeResult]:
        if self._stage:
            self._stage("read", "正在读取和校验考勤文件")
        data = self._reader.read(input_path)
        results: list[OvertimeResult] = []

        if self._stage:
            self._stage("calculate", f"正在计算 {len(data.daily_records)} 条考勤记录")
        for index, day in enumerate(data.daily_records, 1):
            key = (person_key(day.user_id, day.employee_name), day.work_date)
            punches = data.raw_punches.get(key, [])
            results.append(self._overtime_rule.calculate(day, punches))
            if self._stage and index % 100 == 0:
                self._stage("calculate", f"已计算 {index} / {len(data.daily_records)} 条考勤记录")

        results, self.last_input_issues = self._resolve_input_issues(data, results)
        return self._allocate_monthly_late_grace(results)

    def _resolve_input_issues(
        self, data: AttendanceData, results: list[OvertimeResult]
    ) -> tuple[list[OvertimeResult], list[InputIssue]]:
        by_day = {
            (person_key(day.user_id, day.employee_name), day.work_date): (index, day)
            for index, day in enumerate(data.daily_records)
        }
        pending: list[InputIssue] = []
        notes: dict[int, list[str]] = defaultdict(list)
        excluded_rows: dict[int, list[int]] = defaultdict(list)
        for issue in data.input_issues:
            key = (person_key(issue.user_id, issue.employee_name), issue.work_date)
            matched_day = by_day.get(key)
            if matched_day is None or not results[matched_day[0]].punch_pair_complete:
                pending.append(issue)
                continue
            index, day = matched_day
            if (issue.issue_type == "原始数据被规则排除"
                and issue.raw_value.replace("：", ":") == "打卡无效:此记录已被更新"):
                excluded_rows[index].append(issue.source_row)
                continue
            if (issue.source_sheet == self._config.sheets.daily_sheet
                and issue.field_name == "关联的审批单" and issue.issue_type == "业务待确认"):
                supplements = approval_parse_issues(day.approval_text, day.work_date.year)
                punches = fallback_punches(day) if results[index].used_daily_fallback else data.raw_punches.get(key, [])
                approved_times = {
                    punch.occurred_at.replace(second=0, microsecond=0)
                    for punch in punches
                    if punch.result == "补卡审批通过"
                    and not any(punch.result.startswith(prefix) for prefix in self._config.overtime.invalid_punch_prefixes)
                }
                resolved = [
                    item for item in supplements
                    if not item.blocks_summary and item.start == item.end and item.start in approved_times
                ]
                if resolved:
                    times = "、".join(dict.fromkeys(f"{item.start:%Y-%m-%d %H:%M}" for item in resolved))
                    notes[index].append(f"补卡申请已自动核对：{times} 与审批通过的有效打卡匹配（按分钟）")
                unmatched = [item for item in supplements if item not in resolved]
                if not unmatched:
                    continue
                issue = replace(
                    issue,
                    message="；".join(
                        f"补卡 {item.start:%Y-%m-%d %H:%M}到{item.end:%Y-%m-%d %H:%M} 未匹配到对应的单点补卡审批通过记录"
                        for item in unmatched
                    ),
                    impact="不使用补卡申请代替打卡；仍按实际有效卡计算。",
                    suggested_action="核对补卡审批状态和时间，补齐有效打卡后重新导出并统计。",
                )
            pending.append(issue)
        for index, rows in excluded_rows.items():
            notes[index].append(
                f"已自动排除 {len(rows)} 条被更新的无效旧打卡（{self._config.sheets.raw_sheet}第 {'、'.join(map(str, rows))} 行）；当天有效打卡完整"
            )
        for index, messages in notes.items():
            results[index] = replace(results[index], notes=results[index].notes + messages)
        return results, pending

    def _allocate_monthly_late_grace(self, results: list[OvertimeResult]) -> list[OvertimeResult]:
        grouped: dict[tuple[str, int, int], list[int]] = defaultdict(list)
        for index, result in enumerate(results):
            employee_key = result.user_id.strip() or result.employee_name.strip()
            grouped[(employee_key, result.work_date.year, result.work_date.month)].append(index)

        allocated = list(results)
        workday = self._config.workday
        for indices in grouped.values():
            # 每次迟到同时消耗次数额度和分钟额度，两个额度任一耗尽即停止宽限。
            indices.sort(key=lambda index: (results[index].work_date, results[index].employee_name))
            occurrence = 0
            grace_used = 0
            for index in indices:
                result = results[index]
                if result.late_minutes <= 0:
                    allocated[index] = replace(result, late_status="无迟到")
                    continue

                occurrence += 1
                remaining_grace = max(workday.late_grace_minutes_per_month - grace_used, 0)
                grace_minutes = 0
                if occurrence <= workday.late_grace_occurrences_per_month:
                    grace_minutes = min(result.late_minutes, remaining_grace)
                grace_used += grace_minutes
                chargeable_minutes = result.late_minutes - grace_minutes
                if grace_minutes == result.late_minutes:
                    status = f"第{occurrence}次迟到：本次宽限 {grace_minutes} 分钟"
                elif grace_minutes:
                    status = (
                        f"第{occurrence}次迟到：宽限 {grace_minutes} 分钟，"
                        f"扣款 {chargeable_minutes} 分钟"
                    )
                else:
                    status = f"第{occurrence}次迟到：扣款 {chargeable_minutes} 分钟"

                allocated[index] = replace(
                    result,
                    late_occurrence_number=occurrence,
                    late_grace_minutes=grace_minutes,
                    late_chargeable_minutes=chargeable_minutes,
                    late_status=status,
                )

        return allocated
