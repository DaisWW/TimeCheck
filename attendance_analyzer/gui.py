from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import io
import os
from pathlib import Path
from queue import Empty, Queue
import sys
import sysconfig
from threading import Thread
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD

from . import cli
from .config import AppConfig
from .config_editor import CompanyHolidayEditor, RulesEditor
from .file_manager import copy_input, is_link, list_entries, managed_path, prepare_import, recycle_entries
from . import ui_theme as ui


def create_root() -> tk.Tk:
    # 部分启动器会清理架构环境变量；拖放组件需要当前 Python 的架构。
    if sys.platform == "win32":
        architecture = "ARM64" if sysconfig.get_platform() == "win-arm64" else "AMD64" if sys.maxsize > 2**32 else "x86"
        os.environ.setdefault("PROCESSOR_ARCHITECTURE", architecture)
    return TkinterDnD.Tk()


class _RunLog(io.TextIOBase):
    def __init__(self, stream, events: Queue):
        self.stream = stream
        self.events = events

    def write(self, text: str) -> int:
        if text:
            self.events.put(("log", text))
            self.stream.write(text)
            self.stream.flush()
        return len(text)

    def flush(self) -> None:
        if not self.stream.closed:
            self.stream.flush()


def run_job(input_path: Path, output_dir: Path, config_path: Path, log_dir: Path, events: Queue) -> None:
    """在工作线程运行；所有界面更新通过队列交给主线程。"""
    completed = total = 0
    code = 5
    log_path = None

    def progress(done: int, count: int, status: str) -> None:
        nonlocal completed, total
        completed, total = done, count
        events.put(("progress", done, count, status))

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"考勤统计_{datetime.now():%Y%m%d_%H%M%S_%f}.log"
        with log_path.open("w", encoding="utf-8-sig") as stream:
            sink = _RunLog(stream, events)
            with redirect_stdout(sink), redirect_stderr(sink):
                print(f"开始统计：{datetime.now():%Y-%m-%d %H:%M:%S}")
                print(f"输入：{input_path}\n本次结果目录：{output_dir}")
                try:
                    code = cli.main(
                        [str(input_path), "-c", str(config_path)],
                        output_dir=output_dir, progress=progress,
                    )
                except Exception:
                    print("程序发生意外错误，请保留本次日志供排查。", file=sys.stderr)
                    traceback.print_exc()
                    code = 5
                print(f"本次结束：已完成 {completed}/{total} 个文件；退出码 {code}")
    except OSError as error:
        events.put(("log", f"无法保存运行日志：{error}\n请将整个软件目录解压到可写位置（如桌面）后重试。\n"))
        code = 3
    finally:
        events.put(("finished", code, log_path, completed, total))


def outcome_message(code: int, completed: int, total: int) -> str:
    if code == 0:
        return f"统计完成：{completed} 个文件。请查看报表中的待审核、待补卡清单。"
    counts = f"已完成 {completed}/{total} 个文件，批次已停止。" if total else "尚未完成统计。"
    actions = {
        2: "配置或路径有误，请按详情修正后重新统计。",
        3: "保存失败，请按详情检查文件占用、写入权限或磁盘空间。",
        4: "输入文件读取或校验失败，请按详情修正源文件后重新统计。",
        5: "程序发生意外错误，请保留本次日志供排查。",
    }
    return f"{counts}\n{actions.get(code, actions[5])}"


class AttendanceWindow:
    def __init__(self, root: tk.Tk, base_dir: Path):
        self.root = root
        self.base_dir = base_dir.resolve()
        self.input_dir = self.base_dir / "input"
        self.output_dir = self.base_dir / "output"
        self.config_path = self.base_dir / "config.json"
        self.events = Queue()
        self.running = False
        self.log_path = None
        self.result_dir = None
        self.status = tk.StringVar(value="添加考勤文件后，点击“开始统计全部”。")
        self.counts = tk.StringVar(value="")
        self.input_summary = tk.StringVar()
        self.output_summary = tk.StringVar()
        self.run_state = tk.StringVar(value="准备就绪")
        self.details_visible = tk.BooleanVar(value=False)
        self.controls = []
        self.trees = {}
        self.empty_hints = {}
        self.panel_hints = {}
        self.tree_paths = {"input": {}, "output": {}}
        root.title("钉钉考勤统计")
        root.geometry("1140x780")
        root.minsize(900, 620)
        root.protocol("WM_DELETE_WINDOW", self.close)
        ui.configure_theme(root)
        toolbar = ttk.Frame(root, style="Toolbar.TFrame", padding=(16, 3))
        toolbar.pack(fill="x")
        rules_button = ttk.Button(toolbar, text="考勤规则", style="Tool.TButton", command=lambda: self.edit_config(RulesEditor))
        rules_button.pack(side="left")
        holidays_button = ttk.Button(
            toolbar, text="公司放假", style="Tool.TButton", command=lambda: self.edit_config(CompanyHolidayEditor)
        )
        holidays_button.pack(side="left", padx=(4, 0))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=12, pady=7)
        refresh_button = ttk.Button(toolbar, text="刷新列表", style="Tool.TButton", command=self.refresh_files)
        refresh_button.pack(side="left")
        ttk.Label(toolbar, text="本地处理 · 数据保存在本机", style="Toolbar.TLabel").pack(side="right", padx=8)
        self.controls.extend([rules_button, holidays_button, refresh_button])
        ttk.Separator(root).pack(fill="x")
        footer = ttk.Frame(root, padding=(22, 5))
        footer.pack(side="bottom", fill="x")
        self.state_dot = ttk.Label(footer, text="●", foreground=ui.LINK, font=("Segoe UI", 9))
        self.state_dot.pack(side="left", padx=(0, 8))
        ttk.Label(footer, textvariable=self.run_state, style="Muted.TLabel", font=("Microsoft YaHei UI", 9)).pack(side="left")
        ttk.Label(footer, text="规则与放假安排自动保留", style="Muted.TLabel", font=("Microsoft YaHei UI", 9)).pack(side="right")
        ttk.Separator(root).pack(side="bottom", fill="x")
        frame = ttk.Frame(root, padding=(22, 14, 22, 8))
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        logo = tk.Canvas(header, width=34, height=34, background=ui.ACCENT, highlightthickness=0)
        logo.pack(side="left", padx=(0, 12))
        logo.create_rectangle(9, 10, 25, 25, outline=ui.ON_ACCENT, width=2)
        logo.create_line(9, 15, 25, 15, fill=ui.ON_ACCENT, width=2)
        for x in (13, 21):
            logo.create_line(x, 7, x, 12, fill=ui.ON_ACCENT, width=2)
        logo.create_line(13, 20, 16, 23, 21, 18, fill=ui.ON_ACCENT, width=2)
        ttk.Label(header, text="统计考勤", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="添加原始文档，批量统计并生成报表。", style="Muted.TLabel").pack(side="left", padx=(18, 0), pady=(7, 0))
        lists = ttk.Frame(frame)
        lists.grid(row=1, column=0, sticky="nsew")
        lists.columnconfigure((0, 1), weight=1, uniform="files")
        lists.rowconfigure(0, weight=1, minsize=150)
        self.make_file_panel(lists, "input", "原始考勤", 0)
        self.make_file_panel(lists, "output", "统计结果", 1)
        runtime = ttk.Frame(frame, style="Surface.TFrame", padding=(14, 8))
        runtime.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        runtime.columnconfigure(0, weight=1)
        self.run_title = ttk.Label(runtime, textvariable=self.run_state, style="Section.Surface.TLabel", foreground=ui.LINK)
        self.run_title.grid(row=0, column=0, sticky="w")
        ttk.Label(runtime, textvariable=self.counts, style="Muted.Surface.TLabel").grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.status_label = ttk.Label(runtime, textvariable=self.status, style="Muted.Surface.TLabel", wraplength=1000)
        self.status_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        runtime.bind("<Configure>", lambda event: self.status_label.configure(wraplength=max(100, event.width - 28)))
        track = ttk.Frame(runtime, height=4)
        track.grid(row=2, column=0, columnspan=2, sticky="ew")
        track.pack_propagate(False)
        self.progress = ttk.Progressbar(track, mode="determinate")
        self.progress.pack(fill="both", expand=True)
        self.details_frame = ttk.Frame(frame, style="Surface.TFrame", padding=(12, 6, 12, 8))
        self.details_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        detail_actions = ttk.Frame(self.details_frame, style="Surface.TFrame")
        detail_actions.pack(fill="x")
        ttk.Label(detail_actions, text="处理记录", style="Section.Surface.TLabel").pack(side="left")
        ttk.Button(detail_actions, text="复制详情", style="Link.TButton", command=self.copy_details).pack(side="right")
        self.log_button = ttk.Button(detail_actions, text="打开本次日志", style="Link.TButton", command=lambda: self.open_path(self.log_path), state="disabled")
        self.log_button.pack(side="right", padx=8)
        detail_area = ttk.Frame(self.details_frame, style="Surface.TFrame")
        detail_area.pack(fill="both", expand=True, pady=(4, 0))
        self.details = tk.Text(detail_area, wrap="word", height=3, font=("Microsoft YaHei UI", 9),
                               background=ui.PAPER, foreground=ui.TEXT, relief="flat", borderwidth=0,
                               selectbackground=ui.TINT, selectforeground=ui.TEXT, insertbackground=ui.TEXT,
                               highlightthickness=0, padx=2, pady=4, state="disabled")
        detail_scrollbar = ttk.Scrollbar(detail_area, orient="vertical", command=self.details.yview)
        detail_scrollbar.pack(side="right", fill="y")
        self.details.configure(yscrollcommand=detail_scrollbar.set)
        self.details.pack(side="left", fill="both", expand=True)
        self.toggle_details()
        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(actions, text="显示处理详情", variable=self.details_visible, command=self.toggle_details).pack(side="left")
        self.start_button = ttk.Button(actions, text="开始统计全部", style="Accent.TButton", command=self.start)
        self.start_button.pack(side="right")
        self.result_button = ttk.Button(actions, text="打开本次结果", command=lambda: self.open_path(self.result_dir), state="disabled")
        self.result_button.pack(side="right", padx=(0, 10))
        self.controls.append(self.start_button)
        root.bind("<Configure>", self.resize_layout)
        for target in (root, self.trees["input"], self.drop_hint):
            target.drop_target_register(DND_FILES)
            target.dnd_bind("<<Drop>>", self.drop_files)
        self.refresh_files()
        self.poll_id = root.after(100, self.poll)

    def make_file_panel(self, parent: ttk.Frame, kind: str, title: str, column: int) -> None:
        border = tk.Frame(parent, background=ui.PAPER, highlightbackground=ui.LINE, highlightthickness=1)
        border.grid(row=0, column=column, sticky="nsew", padx=(0, 7) if column == 0 else (7, 0))
        panel = ttk.Frame(border, style="Surface.TFrame", padding=(10, 8, 10, 4))
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(3, weight=1, minsize=80)
        heading = ttk.Frame(panel, style="Surface.TFrame")
        heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(heading, text=title, style="Section.Surface.TLabel").pack(side="left")
        summary = self.input_summary if kind == "input" else self.output_summary
        ttk.Label(heading, textvariable=summary, style="Badge.TLabel").pack(side="right")
        toolbar = ttk.Frame(panel, style="Surface.TFrame")
        toolbar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        if kind == "input":
            button = ttk.Button(toolbar, text="添加考勤文件…", command=self.select_files)
            self.controls.append(button)
        else:
            button = ttk.Button(toolbar, text="打开所选", command=lambda: self.open_selected(kind))
        button.pack(side="left")
        remove_button = ttk.Button(toolbar, text="移到回收站", style="Quiet.TButton", command=lambda: self.remove_files(kind))
        remove_button.pack(side="left", padx=4)
        clear_button = ttk.Button(toolbar, text="清空…", style="Danger.TButton", command=lambda: self.remove_files(kind, clear=True))
        clear_button.pack(side="right")
        self.controls.extend([remove_button, clear_button])
        if kind == "input":
            self.drop_hint = tk.Label(panel, text="也可将原始 .xlsx / .xlsm 文件拖到这里", background=ui.TINT, foreground=ui.LINK, font=("Microsoft YaHei UI", 9), pady=5)
            self.drop_hint.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))
            self.panel_hints[kind] = self.drop_hint
        else:
            note = ttk.Label(panel, text="每次统计保留独立批次，可展开查看报表。", style="Muted.Surface.TLabel", padding=(0, 5))
            note.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))
            self.panel_hints[kind] = note
        columns = ("state", "modified") if kind == "input" else ("modified",)
        tree = ttk.Treeview(panel, columns=columns, selectmode="extended", height=8)
        tree.heading("#0", text="文件名" if kind == "input" else "批次 / 结果文件", anchor="w")
        tree.column("#0", width=220, minwidth=220, stretch=False)
        tree.heading("modified", text="更新时间", anchor="w")
        tree.column("modified", width=140, minwidth=130, stretch=False)
        if kind == "input":
            tree.heading("state", text="状态", anchor="w")
            tree.column("state", width=85, minwidth=75, stretch=False)
        tree.tag_configure("alternate", background="#222d3c")
        tree.tag_configure("muted", foreground=ui.MUTED)
        tree.tag_configure("error", foreground=ui.ERROR)
        tree.grid(row=3, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=tree.yview)
        scrollbar.grid(row=3, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)
        horizontal = ttk.Scrollbar(panel, orient="horizontal", command=tree.xview)
        horizontal.grid(row=4, column=0, sticky="ew")
        tree.configure(xscrollcommand=horizontal.set)
        tree.bind("<Double-1>", lambda event: self.open_selected(kind, files_only=True))
        tree.bind("<Delete>", lambda event: self.remove_files(kind))
        self.trees[kind] = tree
        empty = tk.Label(tree, text="还没有原始考勤\n\n添加或拖入文件即可开始" if kind == "input" else "还没有统计结果\n\n生成的报表会按批次显示在这里",
                         background=ui.PAPER, foreground=ui.MUTED, font=("Microsoft YaHei UI", 10), justify="center")
        self.empty_hints[kind] = empty
        tree.bind("<Configure>", lambda event: self.update_empty_hint(kind))
        if kind == "input":
            empty.drop_target_register(DND_FILES)
            empty.dnd_bind("<<Drop>>", self.drop_files)
        footer = ttk.Frame(panel, style="Surface.TFrame")
        footer.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(3, 0))
        ttk.Label(footer, text="双击打开 · Ctrl / Shift 多选", style="Muted.Surface.TLabel").pack(side="left")
        ttk.Button(footer, text=f"打开 {kind} 文件夹", style="Link.TButton", command=lambda: self.open_path(self.base_dir / kind)).pack(side="right")

    def resize_layout(self, event) -> None:
        if event.widget != self.root:
            return
        compact = event.height < 700
        self.details.configure(height=2 if compact else 3)
        for hint in self.panel_hints.values():
            if compact:
                hint.grid_remove()
            else:
                hint.grid()

    def update_empty_hint(self, kind: str) -> None:
        if self.tree_paths[kind] or self.trees[kind].winfo_height() < 140:
            self.empty_hints[kind].place_forget()
        else:
            self.empty_hints[kind].place(relx=0.5, rely=0.55, anchor="center")

    def refresh_files(self) -> bool:
        try:
            try:
                config = AppConfig.load(self.config_path)
            except (OSError, ValueError):
                config = None
            eligible = set(cli._find_input_files(self.input_dir, config)) if config and self.input_dir.is_dir() and not is_link(self.input_dir) else set()
            for kind, tree in self.trees.items():
                selection = tree.selection()
                expanded = {item for item in self.tree_paths[kind] if tree.item(item, "open")}
                tree.delete(*tree.get_children())
                self.tree_paths[kind].clear()
                directory = self.base_dir / kind
                name_width = 220

                def insert(path: Path, parent: str = "") -> None:
                    nonlocal name_width
                    linked = is_link(path)
                    folder = path.is_dir() and not linked
                    item = path.relative_to(directory).as_posix()
                    name_width = max(name_width, int(self.root.tk.call("font", "measure", "TkDefaultFont", path.name)) + 48 + 20 * item.count("/"))
                    modified = datetime.fromtimestamp(path.lstat().st_mtime).strftime("%Y-%m-%d %H:%M")
                    if kind == "input":
                        state = "链接需移除" if linked else "子目录（跳过）" if folder else "待统计" if path in eligible else "规则待修正" if config is None else "不参与统计"
                        values = (state, modified)
                    else:
                        values = (modified,)
                    tags = ["alternate"] if len(self.tree_paths[kind]) % 2 else []
                    if linked or (kind == "input" and config is None):
                        tags.append("error")
                    elif kind == "input" and path not in eligible:
                        tags.append("muted")
                    tree.insert(parent, "end", iid=item, text=path.name, values=values, tags=tags, open=item in expanded or path == self.result_dir)
                    self.tree_paths[kind][item] = path
                    if kind == "output" and folder:
                        for child in list_entries(path):
                            insert(child, item)

                entries = list_entries(directory)
                for path in reversed(entries) if kind == "output" else entries:
                    insert(path)
                tree.column("#0", width=name_width)
                self.update_empty_hint(kind)
                tree.selection_set([item for item in selection if item in self.tree_paths[kind]])
                if kind == "input":
                    self.input_summary.set(f"待统计 {sum(not is_link(path) for path in eligible)} 个文件" if config else "配置有误，请检查 config.json")
                else:
                    self.output_summary.set(f"{len(entries)} 项结果 / 批次")
            return True
        except (OSError, ValueError) as error:
            self.show_error("无法读取数据目录", f"{error}\n请完整解压软件到可写目录，检查 input / output 是否为普通文件夹。")
            return False

    def select_files(self) -> None:
        if self.running:
            return
        paths = filedialog.askopenfilenames(parent=self.root, title="添加钉钉原始考勤文件（可多选）", filetypes=[("Excel 考勤文件", "*.xlsx *.xlsm")])
        if paths:
            self.import_files([Path(path) for path in paths])

    def drop_files(self, event) -> str:
        if self.running:
            self.root.bell()
            return REFUSE_DROP
        try:
            paths = [Path(path) for path in self.root.tk.splitlist(event.data)]
        except (tk.TclError, ValueError) as error:
            self.show_error("无法识别拖入的文件", str(error))
            return REFUSE_DROP
        return COPY if self.import_files(paths) else REFUSE_DROP

    def import_files(self, paths: list[Path]) -> bool:
        if self.running:
            return False
        copied = 0
        try:
            config = AppConfig.load(self.config_path)
            plans = prepare_import(paths, self.input_dir, config.report.output_suffix)
            duplicates = [target.name for _, target in plans if target.exists()]
            replace = False
            if duplicates:
                replace = messagebox.askyesnocancel("input 中已有同名文件", "是否替换以下文件？\n选择“否”跳过同名文件，选择“取消”不导入。\n\n" + "\n".join(duplicates), parent=self.root)
                if replace is None:
                    return False
            self.root.configure(cursor="watch")
            self.root.update_idletasks()
            for source, target in plans:
                if target.exists() and not replace:
                    continue
                copy_input(source, target, replace=replace)
                copied += 1
            if not self.refresh_files():
                return False
            self.set_run_state("准备就绪", ui.SUCCESS)
            self.status_label.configure(foreground=ui.SUCCESS)
            self.status.set(f"已添加 {copied} 个考勤文件，跳过 {len(paths) - copied} 个已存在或重复文件。点击“开始统计全部”处理 input 中全部有效文件。")
            return True
        except (OSError, ValueError) as error:
            self.refresh_files()
            self.show_error("添加考勤文件失败", f"{error}\n本次已添加 {copied} 个文件，原始位置的文件保留。")
            return False
        finally:
            self.root.configure(cursor="")

    def remove_files(self, kind: str, *, clear: bool = False) -> None:
        if self.running:
            return
        try:
            directory = self.base_dir / kind
            paths = list_entries(directory) if clear else [self.tree_paths[kind][item] for item in self.trees[kind].selection()]
            if not paths:
                messagebox.showinfo("没有待清理的项目", "请在列表中选择文件或批次，可按 Ctrl / Shift 多选。", parent=self.root)
                return
            names = [str(path.relative_to(directory)) + ("（文件夹，包含其中全部文件）" if path.is_dir() else "") for path in paths]
            preview = "\n".join(names[:10]) + (f"\n……共 {len(names)} 项" if len(names) > 10 else "")
            prompt = f"将 {kind} 中的以下项目移到回收站？\n\n{preview}\n\n误删可从 Windows 回收站恢复。"
            if kind == "input":
                prompt += "\n只清理 input 中的副本，原始导出位置的文件保留。"
            if not messagebox.askyesno("清空 " + kind if clear else "确认移到回收站", prompt, parent=self.root):
                return
            completed = recycle_entries(directory, paths)
            self.status.set(f"已将 {completed} 项移到回收站。")
        except (OSError, ValueError) as error:
            self.show_error("清理未完成", str(error))
        finally:
            self.refresh_files()

    def open_selected(self, kind: str, *, files_only: bool = False) -> None:
        selected = self.trees[kind].selection()
        if selected:
            path = self.tree_paths[kind][selected[0]]
            if not files_only or path.is_file():
                self.open_path(path)
        elif not files_only:
            messagebox.showinfo("请先选择结果", "请在 output 列表中选择报表或批次。", parent=self.root)

    def toggle_details(self) -> None:
        if self.details_visible.get():
            self.details_frame.grid()
        else:
            self.details_frame.grid_remove()

    def set_run_state(self, text: str, color: str) -> None:
        self.run_state.set(text)
        self.state_dot.configure(foreground=color)
        self.run_title.configure(foreground=color)

    def show_error(self, title: str, message: str) -> None:
        self.set_run_state("需要处理", ui.ERROR)
        self.status.set(title + "，请查看处理详情。")
        self.status_label.configure(foreground=ui.ERROR)
        self.append_details(message + "\n")
        self.details_visible.set(True)
        self.toggle_details()
        messagebox.showerror(title, message, parent=self.root)

    def edit_config(self, editor_type) -> None:
        if self.running:
            return
        try:
            config = AppConfig.load(self.config_path)
        except (OSError, ValueError) as error:
            self.show_error("无法编辑配置", f"{error}\n请检查软件同目录的 config.json，或保留原配置后从“配置模板”恢复。")
            return
        editor = editor_type(self.root, self.config_path, config)
        self.root.wait_window(editor)
        if editor.saved:
            self.set_run_state("准备就绪", ui.LINK)
            self.status_label.configure(foreground=ui.MUTED)
            self.status.set("配置已保存，下次统计将使用新设置。")
            self.refresh_files()

    def start(self) -> None:
        if self.running:
            return
        if not self.refresh_files():
            return
        try:
            config = AppConfig.load(self.config_path)
            inputs = cli._find_input_files(self.input_dir, config)
            if not inputs:
                messagebox.showinfo("请先添加考勤", "input 中没有可统计的考勤文件。\n请点击“添加考勤文件…”或拖入钉钉导出的 .xlsx / .xlsm。", parent=self.root)
                return
            for path in inputs:
                managed_path(self.input_dir, path)
        except (OSError, ValueError) as error:
            self.show_error("无法开始统计", str(error))
            return
        self.result_dir = self.output_dir / f"统计_{datetime.now():%Y%m%d_%H%M%S_%f}"
        self.log_path = None
        self.running = True
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.configure(state="disabled")
        for control in self.controls:
            control.configure(state="disabled")
        self.start_button.configure(text="正在统计…")
        self.result_button.configure(state="disabled")
        self.log_button.configure(state="disabled")
        self.set_run_state("正在统计", ui.LINK)
        self.status_label.configure(foreground=ui.LINK)
        self.status.set("正在检查规则与输入，请稍候……")
        self.counts.set("准备中")
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)
        Thread(target=run_job, args=(self.input_dir, self.result_dir, self.config_path, self.base_dir / "logs", self.events), daemon=True).start()

    def append_details(self, text: str) -> None:
        self.details.configure(state="normal")
        self.details.insert("end", text)
        self.details.see("end")
        self.details.configure(state="disabled")

    def poll(self) -> None:
        try:
            for _ in range(200):
                event, *values = self.events.get_nowait()
                if event == "log":
                    self.append_details(values[0])
                elif event == "progress":
                    completed, total, status = values
                    self.status.set(status)
                    self.counts.set(f"已完成 {completed}/{total} 个文件")
                    self.progress.stop()
                    self.progress.configure(mode="determinate", maximum=max(total, 1), value=completed)
                elif event == "finished":
                    self.finish(*values)
        except Empty:
            pass
        self.poll_id = self.root.after(100, self.poll)

    def finish(self, code: int, log_path: Path | None, completed: int, total: int) -> None:
        self.running = False
        self.log_path = log_path
        for control in self.controls:
            control.configure(state="normal")
        self.start_button.configure(text="再次统计全部" if code == 0 else "重新统计全部")
        self.set_run_state("统计完成" if code == 0 else "统计未完成", ui.SUCCESS if code == 0 else ui.ERROR)
        self.result_button.configure(state="normal" if self.result_dir and self.result_dir.is_dir() else "disabled")
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=max(total, 1), value=completed)
        self.counts.set(f"已完成 {completed}/{total} 个文件" if total else "未完成")
        self.status.set(outcome_message(code, completed, total))
        self.status_label.configure(foreground=ui.SUCCESS if code == 0 else ui.ERROR)
        self.refresh_files()
        if self.result_dir and self.result_dir.name in self.tree_paths["output"]:
            tree = self.trees["output"]
            tree.selection_set(self.result_dir.name)
            tree.see(self.result_dir.name)
        if log_path and log_path.is_file():
            self.log_button.configure(state="normal")
        if code:
            self.details_visible.set(True)
            self.toggle_details()
            messagebox.showerror("统计未完成", outcome_message(code, completed, total) + "\n\n请查看窗口中的完整详情。", parent=self.root)

    def open_path(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            os.startfile(path)
        except OSError as error:
            messagebox.showerror("无法打开", f"{path}\n{error}", parent=self.root)

    def copy_details(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.details.get("1.0", "end-1c"))

    def close(self) -> None:
        if self.running:
            messagebox.showinfo("正在统计", "请等待本次统计结束后关闭窗口，避免中断结果写入。", parent=self.root)
        else:
            self.root.after_cancel(self.poll_id)
            self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="钉钉考勤统计窗口：固定读取软件同目录的 input 和 config.json，结果保存到 output。")
    parser.parse_args(argv)
    base_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    root = create_root()
    AttendanceWindow(root, base_dir)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
