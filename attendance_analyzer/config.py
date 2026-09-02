from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
import sys
import tomllib

from .time_utils import parse_time


@dataclass(frozen=True)
class SheetConfig:
    daily_sheet: str = "每日统计"
    raw_sheet: str = "原始记录"


@dataclass(frozen=True)
class WorkdayConfig:
    flex_start_begin: time = time(9, 0)
    flex_start_end: time = time(10, 0)
    work_minutes: int = 450
    lunch_break_minutes: int = 90
    late_grace_occurrences_per_month: int = 3
    late_grace_minutes_per_month: int = 30
    count_non_workday_overtime: bool = True
    rest_shift_keywords: tuple[str, ...] = ("休息",)


@dataclass(frozen=True)
class OvertimeConfig:
    dinner_break_minutes: int = 30
    min_overtime_minutes: int = 1
    rounding_unit_minutes: int = 1
    rounding_mode: str = "floor"
    invalid_punch_prefixes: tuple[str, ...] = ("打卡无效",)


@dataclass(frozen=True)
class RestDayConfig:
    fixed_lunch_start: time = time(12, 0)
    fixed_lunch_minutes: int = 90
    approval_difference_tolerance_minutes: int = 30
    holiday_dates: tuple[date, ...] = ()
    dinner_break_start: time = time(18, 30)
    dinner_break_minutes: int = 0


@dataclass(frozen=True)
class ReportConfig:
    output_suffix: str = "_加班分析.xlsx"


@dataclass(frozen=True)
class AppConfig:
    sheets: SheetConfig = field(default_factory=SheetConfig)
    workday: WorkdayConfig = field(default_factory=WorkdayConfig)
    overtime: OvertimeConfig = field(default_factory=OvertimeConfig)
    rest_day: RestDayConfig = field(default_factory=RestDayConfig)
    report: ReportConfig = field(default_factory=ReportConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        if path is None:
            return cls()

        with path.open("rb") as config_file:
            data = tomllib.load(config_file)

        return cls(
            sheets=SheetConfig(
                daily_sheet=_get(data, "sheets", "daily_sheet", default="每日统计"),
                raw_sheet=_get(data, "sheets", "raw_sheet", default="原始记录"),
            ),
            workday=WorkdayConfig(
                flex_start_begin=parse_time(_get(data, "workday", "flex_start_begin", default="09:00")),
                flex_start_end=parse_time(_get(data, "workday", "flex_start_end", default="10:00")),
                work_minutes=int(_get(data, "workday", "work_minutes", default=450)),
                lunch_break_minutes=int(_get(data, "workday", "lunch_break_minutes", default=90)),
                late_grace_occurrences_per_month=int(
                    _get(data, "workday", "late_grace_occurrences_per_month", default=3)
                ),
                late_grace_minutes_per_month=int(
                    _get(data, "workday", "late_grace_minutes_per_month", default=30)
                ),
                count_non_workday_overtime=bool(
                    _get(data, "workday", "count_non_workday_overtime", default=True)
                ),
                rest_shift_keywords=tuple(_get(data, "workday", "rest_shift_keywords", default=["休息"])),
            ),
            overtime=OvertimeConfig(
                dinner_break_minutes=int(_get(data, "overtime", "dinner_break_minutes", default=30)),
                min_overtime_minutes=int(_get(data, "overtime", "min_overtime_minutes", default=1)),
                rounding_unit_minutes=int(_get(data, "overtime", "rounding_unit_minutes", default=1)),
                rounding_mode=str(_get(data, "overtime", "rounding_mode", default="floor")),
                invalid_punch_prefixes=tuple(_get(data, "overtime", "invalid_punch_prefixes", default=["打卡无效"])),
            ),
            rest_day=RestDayConfig(
                fixed_lunch_start=parse_time(_get(data, "rest_day", "fixed_lunch_start", default="12:00")),
                fixed_lunch_minutes=int(_get(data, "rest_day", "fixed_lunch_minutes", default=90)),
                approval_difference_tolerance_minutes=int(
                    _get(data, "rest_day", "approval_difference_tolerance_minutes", default=30)
                ),
                holiday_dates=tuple(_parse_dates(_get(data, "rest_day", "holiday_dates", default=[]))),
                dinner_break_start=parse_time(_get(data, "rest_day", "dinner_break_start", default="18:30")),
                dinner_break_minutes=int(_get(data, "rest_day", "dinner_break_minutes", default=0)),
            ),
            report=ReportConfig(
                output_suffix=str(_get(data, "report", "output_suffix", default="_加班分析.xlsx")),
            ),
        )

    def validate(self) -> None:
        if self.workday.flex_start_begin > self.workday.flex_start_end:
            raise ValueError("workday.flex_start_begin 必须早于 flex_start_end")
        if self.workday.work_minutes <= 0:
            raise ValueError("workday.work_minutes 必须大于 0")
        if self.workday.lunch_break_minutes < 0:
            raise ValueError("workday.lunch_break_minutes 不能小于 0")
        if self.workday.late_grace_occurrences_per_month <= 0:
            raise ValueError("workday.late_grace_occurrences_per_month 必须大于 0")
        if self.workday.late_grace_minutes_per_month < 0:
            raise ValueError("workday.late_grace_minutes_per_month 不能小于 0")
        if self.overtime.dinner_break_minutes < 0:
            raise ValueError("overtime.dinner_break_minutes 不能小于 0")
        if self.overtime.rounding_mode not in {"floor", "ceil", "nearest"}:
            raise ValueError("overtime.rounding_mode 只能是 floor、ceil 或 nearest")
        if self.overtime.rounding_unit_minutes <= 0:
            raise ValueError("overtime.rounding_unit_minutes 必须大于 0")
        if self.rest_day.fixed_lunch_minutes < 0:
            raise ValueError("rest_day.fixed_lunch_minutes 不能小于 0")
        if self.rest_day.approval_difference_tolerance_minutes < 0:
            raise ValueError("rest_day.approval_difference_tolerance_minutes 不能小于 0")
        if self.rest_day.dinner_break_minutes < 0:
            raise ValueError("rest_day.dinner_break_minutes 不能小于 0")


def _get(data: dict, section: str, key: str, default):
    return data.get(section, {}).get(key, default)


def _parse_dates(values) -> list[date]:
    result: list[date] = []
    for value in values:
        try:
            result.append(date.fromisoformat(str(value)))
        except ValueError as error:
            raise ValueError(f"Invalid holiday date: {value!r}") from error
    return result


def find_default_config(explicit_path: str | None) -> Path | None:
    if explicit_path:
        return Path(explicit_path)

    # 支持源码运行和 exe 运行两种场景：先找当前目录，再找 exe 所在目录。
    candidates = [Path.cwd() / "config.toml", Path(sys.executable).resolve().parent / "config.toml"]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None
