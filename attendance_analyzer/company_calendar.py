from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
import io
from pathlib import Path
import re
import sys


@dataclass(frozen=True)
class CompanyHoliday:
    work_date: date
    period: str
    note: str = ""


def find_company_holidays(config_path: Path | None) -> Path | None:
    directories = (config_path.parent,) if config_path else (Path.cwd(), Path(sys.executable).resolve().parent)
    return next((directory / "公司放假.csv" for directory in directories if (directory / "公司放假.csv").exists()), None)


def load_company_holidays(path: Path | None) -> tuple[CompanyHoliday, ...]:
    if path is None:
        return ()
    content = path.read_bytes()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("gb18030")
        except UnicodeDecodeError as error:
            raise ValueError(f"{path}：无法解码，请另存为 CSV UTF-8（逗号分隔）") from error
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    entries: dict[date, CompanyHoliday] = {}
    source_rows: dict[date, int] = {}
    try:
        header = [cell.strip() for cell in next(reader, [])]
        if len(header) != 3 or set(header) != {"日期", "时段", "备注"}:
            raise ValueError(f"{path} 第 1 行：表头必须为 日期,时段,备注；原值 {header!r}")
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            source = f"{path} 第 {reader.line_num} 行，原值 {row!r}"
            if len(row) != 3:
                raise ValueError(f"{source}：必须有3列，每行只配置一个日期")
            values = dict(zip(header, (cell.strip() for cell in row)))
            date_text = values["日期"]
            if re.fullmatch(r"[0-9]{4}([-/])[0-9]{1,2}\1[0-9]{1,2}", date_text) is None:
                raise ValueError(f"{source}：日期必须为 YYYY-MM-DD，例如 2026-10-16")
            try:
                work_date = date(*(int(part) for part in re.split(r"[-/]", date_text)))
            except ValueError as error:
                raise ValueError(f"{source}：非法日期 {date_text!r}") from error
            if values["时段"] not in {"全天", "上午", "下午"}:
                raise ValueError(f"{source}：时段只能填写 全天、上午 或 下午")
            if work_date in entries:
                raise ValueError(f"{source}：同一日期重复或冲突，首次配置在第 {source_rows[work_date]} 行；整天放假请只填一行全天")
            entries[work_date] = CompanyHoliday(work_date, values["时段"], values["备注"])
            source_rows[work_date] = reader.line_num
    except csv.Error as error:
        raise ValueError(f"{path} 第 {reader.line_num} 行：CSV 格式错误：{error}") from error
    return tuple(entries[key] for key in sorted(entries))
