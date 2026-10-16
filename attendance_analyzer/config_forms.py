from __future__ import annotations

from datetime import date

from .config import AppConfig, RULE_FIELDS, parse_rule_text


def parse_date_text(text: str) -> date:
    text = text.strip()
    try:
        value = date.fromisoformat(text)
        if value.isoformat() == text:
            return value
    except ValueError:
        pass
    raise ValueError(f"日期 {text!r} 无效，请选择日历或填写 YYYY-MM-DD，例如 2026-10-19")


ROUNDING_LABELS = {"floor": "向下取整", "ceil": "向上取整", "nearest": "就近取整"}


def rule_form_values(config: AppConfig) -> dict[str, str]:
    data = config.to_dict()
    values = {}
    for section, key, _, _, _ in RULE_FIELDS:
        value = data[section][key]
        if isinstance(value, list):
            value = ";".join(value)
        elif isinstance(value, bool):
            value = "是" if value else "否"
        elif key == "rounding_mode":
            value = ROUNDING_LABELS[value]
        values[f"{section}.{key}"] = str(value)
    return values


def apply_rule_values(config: AppConfig, values: dict[str, str]) -> AppConfig:
    expected = {f"{section}.{key}" for section, key, *_ in RULE_FIELDS}
    if not isinstance(values, dict) or set(values) != expected:
        raise ValueError("考勤规则字段不完整或含未知字段，请重新打开编辑窗口。")
    if any(not isinstance(value, str) for value in values.values()):
        raise ValueError("规则表单的值必须为文字。")
    data = config.to_dict()
    defaults = AppConfig()
    for section, key, _, label, _ in RULE_FIELDS:
        full_key = f"{section}.{key}"
        try:
            data[section][key] = parse_rule_text(values[full_key].strip(), getattr(getattr(defaults, section), key), key)
        except ValueError as error:
            raise ValueError(f"{label}：{error}") from error
    return AppConfig.from_dict(data)


def holiday_range_dates(start: str, end: str) -> tuple[date, ...]:
    bounds = []
    for text, label in ((start, "开始日期"), (end, "结束日期")):
        try:
            bounds.append(parse_date_text(text))
        except ValueError as error:
            raise ValueError(f"{label}：{error}") from error
    count = (bounds[1] - bounds[0]).days + 1
    if count <= 0:
        raise ValueError("结束日期不能早于开始日期，请重新选择。")
    if count > 366:
        raise ValueError(f"当前范围共 {count} 天；单次最多加入366天，请缩短范围后分批添加。")
    return tuple(date.fromordinal(bounds[0].toordinal() + index) for index in range(count))
