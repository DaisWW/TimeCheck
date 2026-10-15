from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from .company_calendar import PERIODS, parse_company_holidays
from .config import AppConfig, RULE_FIELDS, parse_rule_text
from .date_picker import DateField, parse_date_text
from . import ui_theme as ui


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
        ui.configure_theme(self)
        self.path = path
        self.saved = False
        self.title(title)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda event: self.cancel())
        header = ttk.Frame(self, style="Surface.TFrame", padding=(22, 16))
        header.pack(fill="x")
        ttk.Label(header, text=title.removeprefix("编辑"), style="DialogTitle.TLabel").pack(anchor="w")
        ttk.Label(header, text="保存后，下一次统计使用新设置；重新打开会保留配置。", style="Muted.Surface.TLabel").pack(anchor="w", pady=(5, 0))
        ttk.Separator(self).pack(fill="x")
        actions = ttk.Frame(self, padding=(22, 12))
        actions.pack(side="bottom", fill="x")
        ttk.Button(actions, text="保存并关闭", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(actions, text="取消", command=self.cancel).pack(side="right", padx=(0, 10))
        ttk.Label(actions, text="修改仅在保存后生效", style="Muted.TLabel", font=("Microsoft YaHei UI", 9)).pack(side="left")
        ttk.Separator(self).pack(side="bottom", fill="x")
        self.body = ttk.Frame(self, padding=(22, 16))
        self.body.pack(fill="both", expand=True)
        self.content = ttk.Frame(self.body)
        self.content.pack(fill="both", expand=True)
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
        self.geometry("900x760")
        self.minsize(760, 700)
        self.initial = rule_form_values(config)
        self.variables: dict[str, tk.StringVar] = {}
        self.widgets = {}
        self.notebook = ttk.Notebook(self.content)
        self.notebook.pack(fill="both", expand=True)
        tabs = {}
        for section, key, group, label, help_text in RULE_FIELDS:
            if group not in tabs:
                tab = ttk.Frame(self.notebook, style="Surface.TFrame", padding=(20, 16))
                tab.columnconfigure(1, minsize=170)
                tab.columnconfigure(2, weight=1)
                self.notebook.add(tab, text=group)
                tabs[group] = tab
            tab = tabs[group]
            row = tab.grid_size()[1]
            full_key = f"{section}.{key}"
            variable = tk.StringVar(self, value=self.initial[full_key])
            self.variables[full_key] = variable
            ttk.Label(tab, text=label, style="Surface.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 18))
            if key == "count_non_workday_overtime":
                widget = ttk.Checkbutton(tab, variable=variable, text="启用", onvalue="是", offvalue="否", style="Surface.TCheckbutton")
            elif key == "rounding_mode":
                widget = ttk.Combobox(tab, width=16, textvariable=variable, values=tuple(ROUNDING_LABELS.values()), state="readonly")
            else:
                widget = ttk.Entry(tab, width=16, textvariable=variable)
            widget.grid(row=row, column=1, sticky="ew", pady=8)
            hint = ttk.Label(tab, text=help_text, wraplength=300, style="Muted.Surface.TLabel")
            hint.grid(row=row, column=2, sticky="ew", padx=(20, 0), pady=8)
            hint.bind("<Configure>", lambda event, label=hint: label.configure(wraplength=max(140, event.width)))
            self.widgets[full_key] = (widget, tab)
        self.notebook.select(tabs["工作日"])
        ui.center_dialog(self, parent)

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


class _HolidayRangeDialog(tk.Toplevel):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        ui.style_window(self)
        self.title("添加公司放假日期范围")
        self.transient(editor)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", self.cancel)
        today = date.today().isoformat()
        self.variables = {key: tk.StringVar(self, value=value) for key, value in (
            ("start", today), ("end", today), ("period", "全天"), ("note", ""),
        )}
        body = ttk.Frame(self, padding=22)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="选择一天或连续多天，再到逐日列表调整每一天的放假时段。").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 14)
        )
        ttk.Label(body, text="开始日期").grid(row=1, column=0, sticky="w", padx=(0, 8))
        self.start_field = DateField(body, self.variables["start"], self.align_end)
        self.start_field.grid(row=1, column=1, sticky="ew", padx=(0, 16))
        ttk.Label(body, text="结束日期").grid(row=1, column=2, sticky="w", padx=(0, 8))
        self.end_field = DateField(body, self.variables["end"])
        self.end_field.grid(row=1, column=3, sticky="ew")
        ttk.Label(body, text="统一放假时段").grid(row=2, column=0, sticky="w", pady=14)
        periods = ttk.Frame(body)
        periods.grid(row=2, column=1, columnspan=3, sticky="w")
        for period in PERIODS:
            ttk.Radiobutton(periods, text=period, value=period, variable=self.variables["period"]).pack(side="left", padx=(0, 20))
        ttk.Label(body, text="统一备注").grid(row=3, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(body, textvariable=self.variables["note"]).grid(row=3, column=1, columnspan=3, sticky="ew")
        self.preview = ttk.Label(body, wraplength=600)
        self.preview.grid(row=4, column=0, columnspan=4, sticky="w", pady=(14, 8))
        ttk.Label(body, text="包含首尾和周末；已有日期不覆盖。加入列表后，仍需在主弹窗保存。", style="Muted.TLabel", font=("Microsoft YaHei UI", 9)).grid(
            row=5, column=0, columnspan=4, sticky="w"
        )
        actions = ttk.Frame(body)
        actions.grid(row=6, column=0, columnspan=4, sticky="e", pady=(16, 0))
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="left", padx=8)
        ttk.Button(actions, text="加入逐日列表", style="Accent.TButton", command=self.confirm).pack(side="left")
        for key in ("start", "end"):
            self.variables[key].trace_add("write", self.update_preview)
        self.update_preview()
        ui.center_dialog(self, editor)
        self.grab_set()
        self.start_field.entry.focus_set()

    def align_end(self, selected: date) -> None:
        try:
            if parse_date_text(self.variables["end"].get()) < selected:
                self.variables["end"].set(selected.isoformat())
        except ValueError:
            pass

    def update_preview(self, *args) -> None:
        try:
            days = holiday_range_dates(self.variables["start"].get(), self.variables["end"].get())
            text = f"共 {len(days)} 天，加入后每天均可单独选择时段、修改备注或删除。"
            color = ui.LINK
        except ValueError as error:
            text, color = str(error), ui.ERROR
        self.preview.configure(text=text, foreground=color)

    def confirm(self) -> None:
        try:
            self.editor.add_range(**{key: variable.get() for key, variable in self.variables.items()})
        except ValueError as error:
            messagebox.showerror("日期范围未加入", str(error), parent=self)
            return
        self.destroy()

    def cancel(self, event=None) -> str:
        self.destroy()
        return "break"

    def destroy(self) -> None:
        super().destroy()
        self.variables.clear()
        if self.editor.winfo_exists():
            self.editor.grab_set()


class CompanyHolidayEditor(_ConfigDialog):
    def __init__(self, parent: tk.Misc, path: Path, config: AppConfig):
        super().__init__(parent, path, "编辑公司放假")
        self.geometry("1000x700")
        self.minsize(860, 500)
        ttk.Label(self.content, text="全员适用 · 公司放假免考勤，实际工作按休息日加班核算。", style="Muted.TLabel", wraplength=800).pack(anchor="w", pady=(0, 5))
        ttk.Label(self.content, text="上午／下午以午休边界划分；法定节假日请在考勤规则中设置。", style="Muted.TLabel", wraplength=800).pack(anchor="w", pady=(0, 14))
        actions = ttk.Frame(self.content)
        actions.pack(fill="x", pady=(0, 10))
        ttk.Button(actions, text="添加日期或范围…", command=self.open_range).pack(side="left")
        self.summary = ttk.Label(actions)
        self.summary.pack(side="left", padx=12)
        area = ttk.Frame(self.content)
        area.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(area, background=ui.PAPER, highlightbackground=ui.LINE, highlightthickness=1)
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.rows_frame = ttk.Frame(self.canvas, style="Surface.TFrame", padding=(12, 10))
        self.rows_frame.columnconfigure(2, weight=1)
        self.canvas_item = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        self.rows_frame.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.canvas_item, width=event.width))
        self.bind("<MouseWheel>", self.scroll_rows)
        for column, label in enumerate(("日期（可点日历选择）", "放假时段", "备注", "")):
            ttk.Label(self.rows_frame, text=label, style="Muted.Surface.TLabel").grid(row=0, column=column, sticky="w", padx=(0, 12), pady=(0, 12))
        self.rows = []
        self.initial = config.to_dict()["company_holidays"]
        for item in self.initial:
            self.add_row(item, arrange=False)
        self.arrange_rows()
        ui.center_dialog(self, parent)

    def open_range(self):
        return _HolidayRangeDialog(self)

    def add_range(self, start: str, end: str, period: str, note: str) -> None:
        days = holiday_range_dates(start, end)
        existing = {item.work_date: item for item in parse_company_holidays(self.values())}
        conflicts = [day for day in days if day in existing]
        if conflicts:
            details = "、".join(f"{day.isoformat()}（{existing[day].period}）" for day in conflicts[:8])
            if len(conflicts) > 8:
                details += f"等 {len(conflicts)} 天"
            raise ValueError(f"以下日期已有安排：{details}。\n本次未加入任何日期。请调整范围，或取消后在逐日列表中修改已有安排。")
        values = [{"date": day.isoformat(), "period": period, "note": note} for day in days]
        parse_company_holidays(values)
        for item in values:
            self.add_row(item, arrange=False)
        self.rows.sort(key=lambda row: row[0]["date"].get())
        self.arrange_rows()
        self.summary.configure(text=f"已加入 {len(days)} 天；当前共 {len(self.rows)} 天，可逐日修改后保存。")
        self.update_idletasks()
        first_index = next(index for index, row in enumerate(self.rows) if row[0]["date"].get() == days[0].isoformat())
        self.canvas.yview_moveto(first_index / len(self.rows))

    def add_row(self, values: dict | None = None, *, arrange: bool = True) -> None:
        values = values or {"date": "", "period": "全天", "note": ""}
        variables = {key: tk.StringVar(self, value=value) for key, value in values.items()}
        date_entry = DateField(self.rows_frame, variables["date"])
        period = ttk.Combobox(self.rows_frame, width=8, textvariable=variables["period"], values=PERIODS, state="readonly")
        note = ttk.Entry(self.rows_frame, textvariable=variables["note"])
        row = (variables, [date_entry, period, note])
        delete = ttk.Button(self.rows_frame, text="删除", style="Quiet.TButton", command=lambda: self.delete_row(row))
        row[1].append(delete)
        for widget in [*row[1], *date_entry.winfo_children()]:
            # 滚动列表时不让下拉框意外改变放假时段。
            widget.bind("<MouseWheel>", self.scroll_rows)
        self.rows.append(row)
        if arrange:
            self.arrange_rows()
            date_entry.entry.focus_set()
            self.canvas.update_idletasks()
            self.canvas.yview_moveto(1)

    def delete_row(self, row) -> None:
        self.rows.remove(row)
        for widget in row[1]:
            widget.destroy()
        self.arrange_rows()

    def arrange_rows(self) -> None:
        self.summary.configure(text=f"当前共 {len(self.rows)} 天；每一天均可单独修改或删除。")
        for index, (_, widgets) in enumerate(self.rows, 1):
            for column, widget in enumerate(widgets):
                widget.grid(row=index, column=column, sticky="ew", padx=(0, 12), pady=6)

    def scroll_rows(self, event) -> str:
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

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
