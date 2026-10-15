from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from .company_calendar import PERIODS, parse_company_holidays
from .config import AppConfig, RULE_FIELDS, parse_rule_text


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
    data = config.to_dict()
    defaults = AppConfig()
    for section, key, _, label, _ in RULE_FIELDS:
        full_key = f"{section}.{key}"
        try:
            data[section][key] = parse_rule_text(values[full_key].strip(), getattr(getattr(defaults, section), key), key)
        except ValueError as error:
            raise ValueError(f"{label}：{error}") from error
    return AppConfig.from_dict(data)


class _ConfigDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, path: Path, title: str):
        super().__init__(parent)
        self.path = path
        self.saved = False
        self.title(title)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda event: self.cancel())
        self.body = ttk.Frame(self, padding=16)
        self.body.pack(fill="both", expand=True)
        ttk.Label(self.body, text="修改后点击“保存并关闭”，下次统计立即使用新设置。", wraplength=700).pack(anchor="w", pady=(0, 12))
        self.content = ttk.Frame(self.body)
        self.content.pack(fill="both", expand=True)
        actions = ttk.Frame(self.body)
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(actions, text="保存并关闭", command=self.save).pack(side="right")
        ttk.Button(actions, text="取消", command=self.cancel).pack(side="right", padx=8)
        self.grab_set()

    def save(self) -> None:
        raise NotImplementedError

    def dirty(self) -> bool:
        raise NotImplementedError

    def destroy(self) -> None:
        super().destroy()
        # Tk 变量须在主线程释放，避免之后被统计线程的垃圾回收触发 Tcl 调用。
        if hasattr(self, "variables"):
            self.variables.clear()
        if hasattr(self, "rows"):
            self.rows.clear()

    def cancel(self) -> None:
        if self.dirty():
            answer = messagebox.askyesnocancel("尚未保存", "是否保存当前修改？\n选择“否”将放弃本次修改。", parent=self)
            if answer is None:
                return
            if answer:
                self.save()
                return
        self.destroy()

    def persist(self, config: AppConfig) -> None:
        try:
            config.save(self.path)
        except (OSError, ValueError) as error:
            messagebox.showerror("配置未保存", f"{error}\n原配置保留，请修正后重试。", parent=self)
            return
        self.saved = True
        self.destroy()


class RulesEditor(_ConfigDialog):
    def __init__(self, parent: tk.Misc, path: Path, config: AppConfig):
        super().__init__(parent, path, "编辑考勤规则")
        self.geometry("860x730")
        self.minsize(760, 700)
        self.initial = rule_form_values(config)
        self.variables: dict[str, tk.StringVar] = {}
        self.widgets = {}
        self.notebook = ttk.Notebook(self.content)
        self.notebook.pack(fill="both", expand=True)
        tabs = {}
        for section, key, group, label, help_text in RULE_FIELDS:
            if group not in tabs:
                tab = ttk.Frame(self.notebook, padding=14)
                tab.columnconfigure(1, weight=1)
                self.notebook.add(tab, text=group)
                tabs[group] = tab
            tab = tabs[group]
            row = tab.grid_size()[1]
            full_key = f"{section}.{key}"
            variable = tk.StringVar(self, value=self.initial[full_key])
            self.variables[full_key] = variable
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12))
            if key == "count_non_workday_overtime":
                widget = ttk.Checkbutton(tab, variable=variable, text="启用", onvalue="是", offvalue="否")
            elif key == "rounding_mode":
                widget = ttk.Combobox(tab, textvariable=variable, values=tuple(ROUNDING_LABELS.values()), state="readonly")
            else:
                widget = ttk.Entry(tab, textvariable=variable)
            widget.grid(row=row, column=1, sticky="ew")
            ttk.Label(tab, text=help_text, wraplength=700, foreground="#555555").grid(
                row=row + 1, column=0, columnspan=2, sticky="w", pady=(3, 12)
            )
            self.widgets[full_key] = (widget, tab)
        self.notebook.select(tabs["工作日"])

    def values(self) -> dict[str, str]:
        return {key: variable.get() for key, variable in self.variables.items()}

    def dirty(self) -> bool:
        return self.values() != self.initial

    def save(self) -> None:
        values = self.values()
        try:
            # 重新读取以保留同一文件中最新的公司放假安排。
            config = apply_rule_values(AppConfig.load(self.path), values)
        except (OSError, ValueError) as error:
            message = str(error)
            for section, key, _, label, _ in RULE_FIELDS:
                message = message.replace(f"{section}.{key}", label)
            messagebox.showerror("请检查考勤规则", message, parent=self)
            keys = getattr(error, "keys", ())
            for section, key, _, label, _ in RULE_FIELDS:
                full_key = f"{section}.{key}"
                if full_key in keys or str(error).startswith(label + "："):
                    widget, tab = self.widgets[full_key]
                    self.notebook.select(tab)
                    widget.focus_set()
                    break
            return
        self.persist(config)


class CompanyHolidayEditor(_ConfigDialog):
    def __init__(self, parent: tk.Misc, path: Path, config: AppConfig):
        super().__init__(parent, path, "编辑公司放假")
        self.geometry("860x600")
        self.minsize(760, 460)
        ttk.Label(self.content, text="全员适用；日期填 YYYY-MM-DD。上午／下午边界采用考勤规则中的固定午休时段。", wraplength=760).pack(anchor="w", pady=(0, 10))
        ttk.Label(self.content, text="公司放假免考勤，实际工作按休息日加班核算；法定节假日在考勤规则中单独设置。", wraplength=760).pack(anchor="w", pady=(0, 10))
        ttk.Button(self.content, text="添加放假日", command=self.add_row).pack(anchor="w", pady=(0, 10))
        area = ttk.Frame(self.content)
        area.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(area, highlightthickness=0)
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.rows_frame = ttk.Frame(self.canvas)
        self.rows_frame.columnconfigure(2, weight=1)
        self.canvas_item = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        self.rows_frame.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.canvas_item, width=event.width))
        for column, label in enumerate(("日期", "放假时段", "备注", "")):
            ttk.Label(self.rows_frame, text=label).grid(row=0, column=column, sticky="w", pady=(0, 8))
        self.rows = []
        self.initial = config.to_dict()["company_holidays"]
        for item in self.initial:
            self.add_row(item)

    def add_row(self, values: dict | None = None) -> None:
        values = values or {"date": "", "period": "全天", "note": ""}
        variables = {key: tk.StringVar(self, value=value) for key, value in values.items()}
        date_entry = ttk.Entry(self.rows_frame, width=14, textvariable=variables["date"])
        period = ttk.Combobox(self.rows_frame, width=8, textvariable=variables["period"], values=PERIODS, state="readonly")
        note = ttk.Entry(self.rows_frame, textvariable=variables["note"])
        row = (variables, [date_entry, period, note])
        delete = ttk.Button(self.rows_frame, text="删除", command=lambda: self.delete_row(row))
        row[1].append(delete)
        self.rows.append(row)
        self.arrange_rows()
        date_entry.focus_set()
        self.canvas.update_idletasks()
        self.canvas.yview_moveto(1)

    def delete_row(self, row) -> None:
        self.rows.remove(row)
        for widget in row[1]:
            widget.destroy()
        self.arrange_rows()

    def arrange_rows(self) -> None:
        for index, (_, widgets) in enumerate(self.rows, 1):
            for column, widget in enumerate(widgets):
                widget.grid(row=index, column=column, sticky="ew", padx=(0, 8), pady=4)

    def values(self) -> list[dict[str, str]]:
        return [{key: variable.get() if key == "note" else variable.get().strip() for key, variable in variables.items()} for variables, _ in self.rows]

    def dirty(self) -> bool:
        return self.values() != self.initial

    def save(self) -> None:
        try:
            holidays = parse_company_holidays(self.values())
            # 保留同一文件中最新的基础考勤规则。
            config = replace(AppConfig.load(self.path), company_holidays=holidays)
        except (OSError, ValueError) as error:
            messagebox.showerror("请检查公司放假", str(error), parent=self)
            return
        self.persist(config)
