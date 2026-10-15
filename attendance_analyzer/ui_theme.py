"""Shared desktop styling; uses only the bundled Tk/ttk runtime."""

import tkinter as tk
from tkinter import font, ttk
import sys


WINDOW = "#141922"
PAPER = "#1d2531"
SOFT = "#253040"
TEXT = "#edf2f9"
MUTED = "#a1afc3"
LINE = "#364357"
ACCENT = "#3168d5"
LINK = "#9fc1ff"
TINT = "#293f60"
SUCCESS = "#7edbb2"
ERROR = "#ffaaaa"
ON_ACCENT = "#ffffff"


def style_window(window: tk.Misc) -> None:
    window.configure(background=WINDOW)
    if sys.platform != "win32":
        return
    # Use the system title bar; older Windows versions may ignore dark mode.
    import ctypes
    from ctypes import wintypes

    try:
        setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
        setter.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
        setter.restype = ctypes.c_long
    except (AttributeError, OSError):
        return

    def apply_titlebar(event=None):
        if event is not None and event.widget != window:
            return
        enabled = ctypes.c_int(1)
        handle = int(window.wm_frame(), 0)
        if setter(handle, 20, ctypes.byref(enabled), ctypes.sizeof(enabled)):
            setter(handle, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))

    # Transient dialogs acquire their final native frame when first mapped.
    window.bind("<Map>", apply_titlebar, add="+")


def configure_theme(window: tk.Misc) -> None:
    style_window(window)
    root = window._root()
    if getattr(root, "_attendance_theme", False):
        return
    root._attendance_theme = True
    root.configure(background=WINDOW)
    root.option_add("*TCombobox*Listbox.background", PAPER)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", TINT)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        font.nametofont(name, root=root).configure(family="Microsoft YaHei UI", size=10)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("Microsoft YaHei UI", 10), background=WINDOW, foreground=TEXT)
    style.configure("TFrame", background=WINDOW)
    style.configure("Surface.TFrame", background=PAPER)
    style.configure("TLabel", background=WINDOW, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
    style.configure("Toolbar.TFrame", background=SOFT)
    style.configure("Toolbar.TLabel", background=SOFT, foreground=MUTED, font=("Microsoft YaHei UI", 9))
    style.configure("Badge.TLabel", background=TINT, foreground=LINK, font=("Microsoft YaHei UI", 9), padding=(8, 3))
    style.configure("Surface.TLabel", background=PAPER)
    style.configure("Muted.Surface.TLabel", background=PAPER, foreground=MUTED, font=("Microsoft YaHei UI", 9))
    style.configure("Section.Surface.TLabel", background=PAPER, font=("Microsoft YaHei UI", 11, "bold"))
    style.configure("DialogTitle.TLabel", background=PAPER, font=("Microsoft YaHei UI", 16, "bold"))
    style.configure("TSeparator", background=LINE)
    style.configure("TButton", background=PAPER, foreground=TEXT, bordercolor=LINE,
                    lightcolor=PAPER, darkcolor=PAPER, borderwidth=1, relief="flat",
                    padding=(12, 6), width=0, focusthickness=1, focuscolor=LINK)
    style.map("TButton", background=[("disabled", WINDOW), ("pressed", TINT), ("active", SOFT)],
              foreground=[("disabled", "#718097")], bordercolor=[("focus", LINK)])
    style.configure("Accent.TButton", background=ACCENT, foreground=ON_ACCENT, bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, padding=(22, 8))
    style.map("Accent.TButton", background=[("disabled", "#293c5e"), ("pressed", "#2857b8"), ("active", "#3c74e4")],
              foreground=[("disabled", "#90a5c5"), ("!disabled", ON_ACCENT)],
              bordercolor=[("disabled", "#293c5e"), ("!disabled", ACCENT)])
    style.configure("Tool.TButton", background=SOFT, borderwidth=0, padding=(12, 7))
    style.map("Tool.TButton", background=[("pressed", TINT), ("active", "#334158")])
    style.configure("Quiet.TButton", background=PAPER, borderwidth=0, padding=(8, 5))
    style.map("Quiet.TButton", background=[("pressed", TINT), ("active", SOFT)])
    style.configure("Danger.TButton", background=PAPER, foreground=ERROR, borderwidth=0, padding=(8, 5))
    style.map("Danger.TButton", background=[("pressed", "#57333e"), ("active", "#3d2c37")],
              foreground=[("disabled", "#718097"), ("!disabled", ERROR)])
    style.configure("Link.TButton", background=PAPER, foreground=LINK, borderwidth=0,
                    padding=(4, 5), font=("Microsoft YaHei UI", 9))
    style.map("Link.TButton", foreground=[("disabled", "#718097"), ("!disabled", LINK)],
              background=[("pressed", TINT), ("active", SOFT)])
    style.configure("TEntry", fieldbackground=PAPER, foreground=TEXT, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, padding=(8, 6))
    style.map("TEntry", bordercolor=[("focus", LINK)], lightcolor=[("focus", LINK)],
              fieldbackground=[("disabled", WINDOW)], foreground=[("disabled", MUTED)])
    style.configure("TCombobox", fieldbackground=PAPER, background=PAPER, foreground=TEXT,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, padding=(7, 5), arrowsize=13)
    style.map("TCombobox", fieldbackground=[("readonly", PAPER)],
              foreground=[("readonly", TEXT)], selectbackground=[("readonly", TINT)],
              selectforeground=[("readonly", TEXT)], bordercolor=[("focus", LINK)])
    style.configure("TSpinbox", fieldbackground=PAPER, background=SOFT, foreground=TEXT,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, padding=(6, 4))
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=WINDOW, padding=(0, 3), focuscolor=LINK,
                        indicatorbackground=SOFT, indicatorforeground=ON_ACCENT)
        style.map(name, background=[("active", WINDOW)],
                  indicatorbackground=[("selected", ACCENT), ("active", LINE)])
    style.configure("Surface.TCheckbutton", background=PAPER)
    style.map("Surface.TCheckbutton", background=[("active", PAPER)])
    style.configure("Treeview", background=PAPER, fieldbackground=PAPER, foreground=TEXT,
                    bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER, relief="flat",
                    borderwidth=0, rowheight=34, font=("Microsoft YaHei UI", 10))
    style.map("Treeview", background=[("selected", TINT)], foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", background=SOFT, foreground=MUTED,
                    font=("Microsoft YaHei UI", 9), relief="flat", padding=(8, 8))
    style.map("Treeview.Heading", background=[("active", "#334158")])
    style.configure("Horizontal.TProgressbar", troughcolor=SOFT, background=ACCENT,
                    borderwidth=0, lightcolor=ACCENT, darkcolor=ACCENT, thickness=4)
    style.configure("TScrollbar", background="#526176", troughcolor=PAPER, bordercolor=PAPER,
                    lightcolor=PAPER, darkcolor=PAPER, arrowcolor=MUTED, borderwidth=0,
                    arrowsize=11, relief="flat")
    style.map("TScrollbar", background=[("active", "#6b7e96"), ("pressed", "#7c92ad")])
    style.configure("TNotebook", background=WINDOW, bordercolor=LINE, lightcolor=PAPER,
                    darkcolor=LINE, borderwidth=0, tabmargins=(0, 0, 0, 8))
    style.configure("TNotebook.Tab", background=WINDOW, foreground=MUTED, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, highlightcolor=LINE,
                    padding=(18, 9), borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", PAPER)], foreground=[("selected", LINK)])
    style.configure("Calendar.TButton", background=PAPER, borderwidth=0, padding=(8, 7))
    style.map("Calendar.TButton", background=[("pressed", ACCENT), ("active", TINT)],
              foreground=[("pressed", ON_ACCENT), ("disabled", "#64738b")])


def center_dialog(window: tk.Toplevel, parent: tk.Misc) -> None:
    window.update_idletasks()
    width, height = window.winfo_width(), window.winfo_height()
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    x = max(0, min(x, window.winfo_screenwidth() - width))
    y = max(0, min(y, window.winfo_screenheight() - height))
    window.geometry(f"+{x}+{y}")
