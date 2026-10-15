from __future__ import annotations

import calendar
from datetime import date
import tkinter as tk
from tkinter import ttk

from . import ui_theme as ui


WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def parse_date_text(text: str) -> date:
    text = text.strip()
    try:
        value = date.fromisoformat(text)
        if value.isoformat() == text:
            return value
    except ValueError:
        pass
    raise ValueError(f"日期 {text!r} 无效，请选择日历或填写 YYYY-MM-DD，例如 2026-10-19")


class DateField(ttk.Frame):
    def __init__(self, parent, variable: tk.StringVar, on_select=None):
        surface = parent.cget("style") == "Surface.TFrame"
        super().__init__(parent, style="Surface.TFrame" if surface else "TFrame")
        self.variable = variable
        self.on_select = on_select
        self.calendar = None
        self.entry = ttk.Entry(self, width=12, textvariable=variable)
        self.entry.pack(side="left", fill="x", expand=True)
        ttk.Button(self, text="日历", width=4, padding=(6, 5), command=self.open_calendar).pack(side="left", padx=5)
        self.weekday = ttk.Label(self, width=4, style="Muted.Surface.TLabel" if surface else "Muted.TLabel")
        self.weekday.pack(side="left")
        self.trace = variable.trace_add("write", self.update_weekday)
        self.update_weekday()

    def update_weekday(self, *args) -> None:
        try:
            text = WEEKDAYS[parse_date_text(self.variable.get()).weekday()]
        except ValueError:
            text = ""
        self.weekday.configure(text=text)

    def open_calendar(self) -> None:
        if self.calendar is not None and self.calendar.winfo_exists():
            self.calendar.lift()
            return
        self.calendar = _CalendarPopup(self)

    def destroy(self) -> None:
        self.variable.trace_remove("write", self.trace)
        super().destroy()
        self.variable = None
        self.on_select = None


class _CalendarPopup(tk.Toplevel):
    def __init__(self, field: DateField):
        super().__init__(field)
        ui.style_window(self)
        self.configure(background=ui.PAPER)
        self.withdraw()
        self.title("选择日期")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.resizable(False, False)
        self.transient(field.winfo_toplevel())
        self.previous_grab = self.grab_current()
        self.target = field.variable
        self.on_select = field.on_select
        try:
            self.selected = parse_date_text(self.target.get())
        except ValueError:
            self.selected = date.today()
        body = ttk.Frame(self, style="Surface.TFrame", padding=16)
        body.pack(fill="both", expand=True)
        header = ttk.Frame(body, style="Surface.TFrame")
        header.pack(fill="x", pady=(0, 8))
        self.previous = ttk.Button(header, text="‹", width=3, style="Quiet.TButton", command=lambda: self.move_month(-1))
        self.previous.pack(side="left")
        self.year = ttk.Spinbox(header, from_=1, to=9999, width=6, command=self.render)
        self.year.insert(0, str(self.selected.year))
        self.year.pack(side="left", padx=(8, 2))
        ttk.Label(header, text="年", style="Surface.TLabel").pack(side="left")
        self.month = ttk.Spinbox(header, from_=1, to=12, width=3, command=self.render)
        self.month.insert(0, str(self.selected.month))
        self.month.pack(side="left", padx=(8, 2))
        ttk.Label(header, text="月", style="Surface.TLabel").pack(side="left", padx=(0, 8))
        self.next = ttk.Button(header, text="›", width=3, style="Quiet.TButton", command=lambda: self.move_month(1))
        self.next.pack(side="right")
        for entry in (self.year, self.month):
            for event in ("<KeyRelease>", "<FocusOut>"):
                entry.bind(event, self.render)
        days = ttk.Frame(body, style="Surface.TFrame")
        days.pack(fill="x")
        for column, text in enumerate("一二三四五六日"):
            ttk.Label(days, text=text, anchor="center", style="Muted.Surface.TLabel").grid(row=0, column=column, sticky="ew", pady=(6, 8))
            days.columnconfigure(column, weight=1)
        self.buttons = []
        for index in range(42):
            button = ttk.Button(days, width=3, style="Calendar.TButton")
            button.grid(row=index // 7 + 1, column=index % 7, padx=2, pady=2, sticky="ew")
            self.buttons.append(button)
        self.hint = ttk.Label(body, style="Muted.Surface.TLabel")
        self.hint.pack(anchor="w", pady=8)
        ttk.Button(body, text="今天", style="Link.TButton", command=lambda: self.choose(date.today())).pack(side="left")
        ttk.Button(body, text="取消", style="Quiet.TButton", command=self.destroy).pack(side="right")
        self.bind("<Escape>", self.cancel)
        self.render()
        self.update_idletasks()
        x = max(0, min(field.winfo_rootx(), self.winfo_screenwidth() - self.winfo_reqwidth()))
        y = max(0, min(field.winfo_rooty() + field.winfo_height(), self.winfo_screenheight() - self.winfo_reqheight()))
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.grab_set()
        self.focus_set()

    def month_value(self) -> tuple[int, int]:
        year, month = self.year.get(), self.month.get()
        if len(year) > 4 or len(month) > 2 or not year.isascii() or not year.isdecimal() or not month.isascii() or not month.isdecimal():
            raise ValueError
        year, month = int(year), int(month)
        if not 1 <= year <= 9999 or not 1 <= month <= 12:
            raise ValueError
        return year, month

    def render(self, event=None) -> None:
        for button in self.buttons:
            button.configure(text="", state="disabled", command="")
            button.state(["!pressed"])
        try:
            year, month = self.month_value()
        except ValueError:
            self.hint.configure(text="请填写有效年月：年份1–9999，月份1–12。", foreground=ui.ERROR)
            self.previous.configure(state="disabled")
            self.next.configure(state="disabled")
            return
        self.previous.configure(state="disabled" if (year, month) == (1, 1) else "normal")
        self.next.configure(state="disabled" if (year, month) == (9999, 12) else "normal")
        for row, week in enumerate(calendar.Calendar().monthdayscalendar(year, month)):
            for column, day in enumerate(week):
                if day:
                    value = date(year, month, day)
                    button = self.buttons[row * 7 + column]
                    button.configure(text=str(day), state="normal", command=lambda chosen=value: self.choose(chosen))
                    if value == self.selected:
                        button.state(["pressed"])
        self.hint.configure(text=f"当前：{self.selected.isoformat()}（{WEEKDAYS[self.selected.weekday()]}）", foreground=ui.MUTED)

    def move_month(self, offset: int) -> None:
        year, month = self.month_value()
        index = (year - 1) * 12 + month - 1 + offset
        if not 0 <= index < 9999 * 12:
            return
        year, month = divmod(index, 12)
        for entry, value in ((self.year, year + 1), (self.month, month + 1)):
            entry.delete(0, "end")
            entry.insert(0, str(value))
        self.render()

    def choose(self, value: date) -> None:
        self.target.set(value.isoformat())
        if self.on_select is not None:
            self.on_select(value)
        self.destroy()

    def cancel(self, event=None) -> str:
        self.destroy()
        return "break"

    def destroy(self) -> None:
        previous = self.previous_grab
        self.previous_grab = None
        super().destroy()
        self.target = None
        self.on_select = None
        if previous is not None and previous.winfo_exists():
            previous.grab_set()
