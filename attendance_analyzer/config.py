from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, time
import json
from pathlib import Path
import re
import tempfile

from .company_calendar import CompanyHoliday, parse_company_holidays
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
        if path is None:
            return cls()
        _check_json_path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_json_keys, parse_constant=_reject_json_constant)
            return cls.from_dict(data)
        except ValueError as error:
            raise ValueError(f"{path}：{error}") from error

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        _require_keys(data, {*_SECTION_TYPES, "company_holidays"}, "配置")
        defaults = cls()
        sections = {}
        for section, section_type in _SECTION_TYPES.items():
            raw = data[section]
            _require_keys(raw, {item.name for item in fields(section_type)}, section)
            values = {}
            for item in fields(section_type):
                key = f"{section}.{item.name}"
                try:
                    values[item.name] = _parse_json_value(raw[item.name], getattr(getattr(defaults, section), item.name), item.name)
                except ValueError as error:
                    raise _ConfigValidationError(f"{key}：{error}；原值 {raw[item.name]!r}", key) from error
            sections[section] = section_type(**values)
        config = cls(**sections, company_holidays=parse_company_holidays(data["company_holidays"]))
        try:
            config.validate()
        except _ConfigValidationError as error:
            originals = ", ".join(f"{key}={data[key.split('.')[0]][key.split('.')[1]]!r}" for key in error.keys)
            raise _ConfigValidationError(f"{error}；原值 {originals}", *error.keys) from error
        return config

    def to_dict(self) -> dict:
        data = {
            section: {item.name: _json_value(getattr(getattr(self, section), item.name)) for item in fields(section_type)}
            for section, section_type in _SECTION_TYPES.items()
        }
        data["company_holidays"] = [
            {"date": item.work_date.isoformat(), "period": item.period, "note": item.note}
            for item in self.company_holidays
        ]
        return data

    def save(self, path: Path) -> None:
        _check_json_path(path)
        data = self.from_dict(self.to_dict()).to_dict()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.stem}_", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def validate(self) -> None:
        if not self.sheets.daily_sheet.strip() or not self.sheets.raw_sheet.strip():
            raise _ConfigValidationError("工作表名称不能为空", "sheets.daily_sheet", "sheets.raw_sheet")
        if self.sheets.daily_sheet == self.sheets.raw_sheet:
            raise _ConfigValidationError("每日统计和原始记录不能使用同一工作表", "sheets.daily_sheet", "sheets.raw_sheet")
        if self.workday.flex_start_begin > self.workday.flex_start_end:
            raise _ConfigValidationError("workday.flex_start_begin 必须早于 workday.flex_start_end", "workday.flex_start_begin", "workday.flex_start_end")
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


_SECTION_TYPES = {
    "sheets": SheetConfig,
    "workday": WorkdayConfig,
    "overtime": OvertimeConfig,
    "rest_day": RestDayConfig,
    "report": ReportConfig,
}


RULE_FIELDS = (
    ('sheets', 'daily_sheet', '工作表', '每日统计表名称', '钉钉导出中每日统计工作表的名称'),
    ('sheets', 'raw_sheet', '工作表', '原始记录表名称', '钉钉导出中原始打卡工作表的名称'),
    ('workday', 'flex_start_begin', '工作日', '弹性上班开始时间', '早于此时间到岗也从此时间计算标准下班；格式为09:00'),
    ('workday', 'flex_start_end', '工作日', '弹性上班结束时间', '晚于此时间到岗不再顺延下班；格式为10:00'),
    ('workday', 'work_minutes', '工作日', '每日工作分钟', '450分钟等于7.5小时；填写整数分钟'),
    ('workday', 'lunch_break_minutes', '工作日', '午休分钟', '用于推算标准下班时间；请假和迟到扣除时段见非工作日的固定午休设置'),
    ('workday', 'late_grace_occurrences_per_month', '工作日', '每月迟到宽限次数', '每人每月可宽限的迟到次数；次数或分钟额度先用完即停止宽限'),
    ('workday', 'late_grace_minutes_per_month', '工作日', '每月迟到宽限分钟', '每人每月累计可宽限的迟到分钟；填0表示无迟到宽限'),
    ('workday', 'count_non_workday_overtime', '工作日', '统计非工作日加班', '控制是否计算休息日和法定节假日加班'),
    ('workday', 'rest_shift_keywords', '工作日', '休息班次关键字', '班次包含这些字样即视为休息；多个关键字用分号分隔；留空表示无'),
    ('overtime', 'dinner_break_minutes', '加班', '工作日晚餐扣除分钟', '标准下班后再经过这些分钟才开始算加班；填0表示不扣'),
    ('overtime', 'min_overtime_minutes', '加班', '最小加班分钟', '不足这些分钟不计加班；只填非负整数'),
    ('overtime', 'rounding_unit_minutes', '加班', '取整单位分钟', '填1表示精确到分钟；填30表示按半小时取整'),
    ('overtime', 'rounding_mode', '加班', '取整方式', '就近取整正好半格时取偶数格'),
    ('overtime', 'invalid_punch_prefixes', '加班', '无效打卡前缀', '打卡结果以这些字样开头时排除该卡；多个前缀用分号分隔；留空表示无'),
    ('rest_day', 'fixed_lunch_start', '非工作日', '固定午休开始时间', '固定午休起点；也用于工作日请假和迟到抵扣；格式为12:00'),
    ('rest_day', 'fixed_lunch_minutes', '非工作日', '固定午休分钟', '从固定午休起点持续的分钟数；也用于工作日请假和迟到抵扣'),
    ('rest_day', 'approval_difference_tolerance_minutes', '非工作日', '审批差异容差分钟', '审批与实际加班相差超过此值时交HR审核；只填非负整数'),
    ('rest_day', 'holiday_dates', '非工作日', '法定节假日日期', '仅填写法定节假日；例如2026-10-01;2026-10-02；留空表示未配置；调休工作日以班次为准'),
    ('rest_day', 'dinner_break_start', '非工作日', '晚餐开始时间', '非工作日晚餐扣除时段的起点；格式为18:30'),
    ('rest_day', 'dinner_break_minutes', '非工作日', '晚餐扣除分钟', '填0表示不扣非工作日晚餐；需要扣半小时则填30'),
    ('report', 'output_suffix', '报表', '输出文件后缀', '接在原考勤文件名后；必须以.xlsx结尾且不能包含路径'),
)


def _check_json_path(path: Path) -> None:
    if path.suffix.lower() != ".json":
        raise ValueError(f"{path}：配置文件仅支持 .json，请在窗口选择 JSON 配置文件")


def _unique_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"配置项重复 {key!r}；原值 {value!r}")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError(f"不支持的 JSON 数值：{value}")


def _require_keys(data, expected: set[str], label: str) -> None:
    if not isinstance(data, dict):
        raise ValueError(f"{label} 必须是对象；原值 {data!r}")
    unknown, missing = set(data) - expected, expected - set(data)
    if unknown or missing:
        raise ValueError(f"{label} 配置项不完整：未知项 {sorted(unknown)!r}，缺少项 {sorted(missing)!r}；原值 {data!r}")


def _json_value(value):
    if isinstance(value, (time, date)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _parse_json_value(value, default, key: str):
    expected = str if isinstance(default, time) else list if isinstance(default, tuple) else type(default)
    if type(value) is not expected:
        raise ValueError(f"类型错误，应为 {expected.__name__}")
    if isinstance(default, time):
        try:
            return parse_time(value)
        except ValueError as error:
            raise ValueError("时间必须为有效的 HH:MM 或 HH:MM:SS") from error
    if isinstance(default, tuple):
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("列表必须由非空字符串组成")
        if key == "holiday_dates":
            if any(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", item) is None for item in value):
                raise ValueError("法定节假日日期必须为 YYYY-MM-DD")
            try:
                return tuple(date.fromisoformat(item) for item in value)
            except ValueError as error:
                raise ValueError("法定节假日含非法日期") from error
        return tuple(value)
    return value


def parse_rule_text(value: str, default, key: str):
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
        raise ValueError("值不能为空")
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


def find_default_config(explicit_path: str | None) -> Path | None:
    if explicit_path:
        return Path(explicit_path)
    path = Path.cwd() / "config.json"
    return path if path.exists() else None
