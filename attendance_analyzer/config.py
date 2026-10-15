from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, time
import io
from pathlib import Path
import re
import sys

from .company_calendar import CompanyHoliday, find_company_holidays, load_company_holidays
from .time_utils import parse_date_text, parse_time


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


class _ConfigValidationError(ValueError):
    def __init__(self, message: str, *keys: str):
        super().__init__(message)
        self.keys = keys


@dataclass(frozen=True)
class AppConfig:
    sheets: SheetConfig = field(default_factory=SheetConfig)
    workday: WorkdayConfig = field(default_factory=WorkdayConfig)
    overtime: OvertimeConfig = field(default_factory=OvertimeConfig)
    rest_day: RestDayConfig = field(default_factory=RestDayConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    company_holidays: tuple[CompanyHoliday, ...] = ()

    def company_holiday_on(self, work_date: date) -> CompanyHoliday | None:
        return next((item for item in self.company_holidays if item.work_date == work_date), None)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        if path is not None and path.suffix.lower() != ".csv":
            raise ValueError(f"{path}：配置文件仅支持 .csv")
        company_holidays = load_company_holidays(find_company_holidays(path))
        if path is None:
            return cls(company_holidays=company_holidays)

        data, csv_sources = _load_csv(path)

        config = cls(
            company_holidays=company_holidays,
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
        try:
            config.validate()
        except _ConfigValidationError as error:
            sources = "\n".join(csv_sources[key] for key in error.keys if key in csv_sources)
            raise ValueError(f"{path}：{error}\n{sources}") from error
        return config

    def validate(self) -> None:
        if not self.sheets.daily_sheet.strip() or not self.sheets.raw_sheet.strip():
            raise _ConfigValidationError("工作表名称不能为空", "sheets.daily_sheet", "sheets.raw_sheet")
        if self.sheets.daily_sheet == self.sheets.raw_sheet:
            raise _ConfigValidationError("每日统计和原始记录不能使用同一工作表", "sheets.daily_sheet", "sheets.raw_sheet")
        if self.workday.flex_start_begin > self.workday.flex_start_end:
            raise _ConfigValidationError("workday.flex_start_begin 必须早于 flex_start_end", "workday.flex_start_begin", "workday.flex_start_end")
        if self.workday.work_minutes <= 0:
            raise _ConfigValidationError("workday.work_minutes 必须大于 0", "workday.work_minutes")
        if self.workday.lunch_break_minutes < 0:
            raise _ConfigValidationError("workday.lunch_break_minutes 不能小于 0", "workday.lunch_break_minutes")
        if self.workday.late_grace_occurrences_per_month <= 0:
            raise _ConfigValidationError("workday.late_grace_occurrences_per_month 必须大于 0", "workday.late_grace_occurrences_per_month")
        if self.workday.late_grace_minutes_per_month < 0:
            raise _ConfigValidationError("workday.late_grace_minutes_per_month 不能小于 0", "workday.late_grace_minutes_per_month")
        if self.overtime.dinner_break_minutes < 0:
            raise _ConfigValidationError("overtime.dinner_break_minutes 不能小于 0", "overtime.dinner_break_minutes")
        if self.overtime.min_overtime_minutes < 0:
            raise _ConfigValidationError("overtime.min_overtime_minutes 不能小于 0", "overtime.min_overtime_minutes")
        if self.overtime.rounding_mode not in {"floor", "ceil", "nearest"}:
            raise _ConfigValidationError("overtime.rounding_mode 只能是 floor、ceil 或 nearest（向下取整、向上取整、就近取整）", "overtime.rounding_mode")
        if self.overtime.rounding_unit_minutes <= 0:
            raise _ConfigValidationError("overtime.rounding_unit_minutes 必须大于 0", "overtime.rounding_unit_minutes")
        if self.rest_day.fixed_lunch_minutes < 0:
            raise _ConfigValidationError("rest_day.fixed_lunch_minutes 不能小于 0", "rest_day.fixed_lunch_minutes")
        if self.rest_day.approval_difference_tolerance_minutes < 0:
            raise _ConfigValidationError("rest_day.approval_difference_tolerance_minutes 不能小于 0", "rest_day.approval_difference_tolerance_minutes")
        if self.rest_day.dinner_break_minutes < 0:
            raise _ConfigValidationError("rest_day.dinner_break_minutes 不能小于 0", "rest_day.dinner_break_minutes")
        if not self.report.output_suffix.endswith(".xlsx") or any(char in self.report.output_suffix for char in '/\\:*?"<>|'):
            raise _ConfigValidationError("report.output_suffix 必须是合法的 .xlsx 文件名后缀", "report.output_suffix")


_CSV_FIELDS = {
    ("工作表", "每日统计表名称"): ("sheets", "daily_sheet"),
    ("工作表", "原始记录表名称"): ("sheets", "raw_sheet"),
    ("工作日", "弹性上班开始时间"): ("workday", "flex_start_begin"),
    ("工作日", "弹性上班结束时间"): ("workday", "flex_start_end"),
    ("工作日", "每日工作分钟"): ("workday", "work_minutes"),
    ("工作日", "午休分钟"): ("workday", "lunch_break_minutes"),
    ("工作日", "每月迟到宽限次数"): ("workday", "late_grace_occurrences_per_month"),
    ("工作日", "每月迟到宽限分钟"): ("workday", "late_grace_minutes_per_month"),
    ("工作日", "统计非工作日加班"): ("workday", "count_non_workday_overtime"),
    ("工作日", "休息班次关键字"): ("workday", "rest_shift_keywords"),
    ("加班", "工作日晚餐扣除分钟"): ("overtime", "dinner_break_minutes"),
    ("加班", "最小加班分钟"): ("overtime", "min_overtime_minutes"),
    ("加班", "取整单位分钟"): ("overtime", "rounding_unit_minutes"),
    ("加班", "取整方式"): ("overtime", "rounding_mode"),
    ("加班", "无效打卡前缀"): ("overtime", "invalid_punch_prefixes"),
    ("非工作日", "固定午休开始时间"): ("rest_day", "fixed_lunch_start"),
    ("非工作日", "固定午休分钟"): ("rest_day", "fixed_lunch_minutes"),
    ("非工作日", "审批差异容差分钟"): ("rest_day", "approval_difference_tolerance_minutes"),
    ("非工作日", "法定节假日日期"): ("rest_day", "holiday_dates"),
    ("非工作日", "晚餐开始时间"): ("rest_day", "dinner_break_start"),
    ("非工作日", "晚餐扣除分钟"): ("rest_day", "dinner_break_minutes"),
    ("报表", "输出文件后缀"): ("report", "output_suffix"),
}


def _load_csv(path: Path) -> tuple[dict, dict[str, str]]:
    content = path.read_bytes()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("gb18030")
        except UnicodeDecodeError as error:
            raise ValueError(f"{path}：无法解码配置，请另存为 CSV UTF-8（逗号分隔）") from error

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    data: dict = {}
    sources: dict[str, str] = {}
    defaults = AppConfig()
    try:
        header = next(reader, [])
        header = [cell.strip() for cell in header]
        if len(header) != 4 or set(header) != {"分类", "配置项", "值", "说明"}:
            raise ValueError(f"{path} 第 1 行：表头必须为 分类,配置项,值,说明；原值 {header!r}")
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            source = f"{path} 第 {reader.line_num} 行，原值 {row!r}"
            if len(row) != len(header):
                raise ValueError(f"{source}：必须有4列；多个值请用分号分隔")
            values = dict(zip(header, (cell.strip() for cell in row)))
            field_key = (values["分类"], values["配置项"])
            if field_key not in _CSV_FIELDS:
                raise ValueError(f"{source}：未知分类或配置项 {field_key!r}，请保留模板中的名称")
            section, key = _CSV_FIELDS[field_key]
            full_key = f"{section}.{key}"
            if full_key in sources:
                raise ValueError(f"{source}：配置项重复；首次出现在 {sources[full_key]}")
            try:
                value = _parse_csv_value(values["值"], getattr(getattr(defaults, section), key), key)
            except ValueError as error:
                raise ValueError(f"{source}：{error}") from error
            data.setdefault(section, {})[key] = value
            sources[full_key] = source
    except csv.Error as error:
        raise ValueError(f"{path} 第 {reader.line_num} 行：CSV 格式错误：{error}") from error
    if not sources:
        raise ValueError(f"{path}：CSV 配置没有配置项，请使用完整模板")
    return data, sources


def _parse_csv_value(value: str, default, key: str):
    if isinstance(default, tuple):
        if not value:
            return []
        items = [item.strip() for item in re.split(r"[;；、,，]|\r?\n", value)]
        if any(not item for item in items):
            raise ValueError("列表含空项；多个值请用分号分隔，末尾不加分号")
        if key == "holiday_dates":
            dates = []
            for item in items:
                parsed = parse_date_text(item) if re.fullmatch(r"[0-9]{4}([-/])[0-9]{1,2}\1[0-9]{1,2}", item) else None
                if parsed is None:
                    raise ValueError(f"非法节假日日期 {item!r}；请填写 YYYY-MM-DD，多个日期用分号分隔")
                dates.append(parsed.isoformat())
            return dates
        return items
    if not value:
        raise ValueError("值不能为空；恢复默认值请从模板复制，或删除整行")
    if isinstance(default, bool):
        options = {"是": True, "否": False, "true": True, "false": False}
        if value.lower() not in options:
            raise ValueError("开关只能填写 是、否、true 或 false")
        return options[value.lower()]
    if isinstance(default, int):
        if re.fullmatch(r"[+-]?[0-9]+", value) is None:
            raise ValueError("必须填写整数分钟或次数，不接受小数、单位或公式")
        return int(value)
    if isinstance(default, time):
        try:
            parse_time(value)
        except ValueError as error:
            raise ValueError("时间必须为有效的 HH:MM 或 HH:MM:SS，例如 09:00") from error
    if key == "rounding_mode":
        return {"向下取整": "floor", "向上取整": "ceil", "就近取整": "nearest"}.get(value, value)
    return value


def _get(data: dict, section: str, key: str, default):
    value = data.get(section, {}).get(key, default)
    if type(value) is not type(default):
        raise ValueError(f"{section}.{key} 类型错误：{value!r}；应为 {type(default).__name__}")
    if isinstance(value, list) and key != "holiday_dates":
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"{section}.{key} 必须是非空字符串组成的数组：{value!r}")
    return value


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
    candidates = [Path.cwd() / "config.csv", Path(sys.executable).resolve().parent / "config.csv"]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None
