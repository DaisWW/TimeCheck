from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re


@dataclass(frozen=True)
class CompanyHoliday:
    work_date: date
    period: str
    note: str = ""


PERIODS = ("全天", "上午", "下午")


def parse_company_holidays(values) -> tuple[CompanyHoliday, ...]:
    if not isinstance(values, list):
        raise ValueError(f"company_holidays 必须是公司放假列表；原值 {values!r}")
    entries: dict[date, CompanyHoliday] = {}
    source_rows: dict[date, int] = {}
    for index, row in enumerate(values, 1):
        source = f"公司放假第 {index} 项（company_holidays[{index - 1}]），原值 {row!r}"
        if not isinstance(row, dict) or set(row) != {"date", "period", "note"}:
            raise ValueError(f"{source}：每项必须包含 date、period、note")
        date_text = row["date"]
        if not isinstance(date_text, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date_text) is None:
            raise ValueError(f"{source}：日期必须为 YYYY-MM-DD，例如 2026-10-16")
        try:
            work_date = date.fromisoformat(date_text)
        except ValueError as error:
            raise ValueError(f"{source}：非法日期 {date_text!r}") from error
        if row["period"] not in PERIODS:
            raise ValueError(f"{source}：时段只能选择 全天、上午 或 下午")
        if not isinstance(row["note"], str):
            raise ValueError(f"{source}：备注必须是文字")
        if work_date in entries:
            raise ValueError(f"{source}：同一日期重复或冲突，首次配置在第 {source_rows[work_date]} 项；整天放假请只填一项全天")
        entries[work_date] = CompanyHoliday(work_date, row["period"], row["note"])
        source_rows[work_date] = index
    return tuple(entries[key] for key in sorted(entries))
