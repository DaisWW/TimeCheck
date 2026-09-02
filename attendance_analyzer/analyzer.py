from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from .config import AppConfig
from .excel_reader import DingTalkWorkbookReader, person_key
from .models import InputIssue, OvertimeResult
from .rules import OvertimeRule


class AttendanceAnalyzer:
    def __init__(self, config: AppConfig):
        self._config = config
        self._reader = DingTalkWorkbookReader(config)
        self._overtime_rule = OvertimeRule(config)
        self.last_input_issues: list[InputIssue] = []

    def analyze_overtime(self, input_path: Path) -> list[OvertimeResult]:
        data = self._reader.read(input_path)
        self.last_input_issues = data.input_issues
        results: list[OvertimeResult] = []

        for day in data.daily_records:
            key = (person_key(day.user_id, day.employee_name), day.work_date)
            punches = data.raw_punches.get(key, [])
            results.append(self._overtime_rule.calculate(day, punches))

        return self._allocate_monthly_late_grace(results)

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
