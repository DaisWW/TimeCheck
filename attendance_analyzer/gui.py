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
from tkinter.scrolledtext import ScrolledText

from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD

from . import cli
from .config import AppConfig
from .config_editor import CompanyHolidayEditor, RulesEditor
from .file_manager import copy_input, is_link, list_entries, managed_path, prepare_import, recycle_entries


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
        self.details_visible = tk.BooleanVar(value=False)
        self.controls = []
        self.trees = {}
        self.tree_paths = {"input": {}, "output": {}}
        root.title("钉钉考勤统计")
        root.geometry("1020x720")
        root.minsize(900, 620)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(8, 4))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        header = ttk.Frame(frame)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="考勤统计", style="Title.TLabel").pack(side="left")
        holidays_button = ttk.Button(
            header, text="编辑公司放假", command=lambda: self.edit_config(CompanyHolidayEditor)
        )
        holidays_button.pack(side="right")
        rules_button = ttk.Button(header, text="编辑考勤规则", command=lambda: self.edit_config(RulesEditor))
        rules_button.pack(side="right", padx=8)
        self.controls.extend([rules_button, holidays_button])
        ttk.Label(frame, text="添加原始考勤 → 统计 input 中全部文件 → 在 output 列表查看结果。").grid(
            row=1, column=0, sticky="w", pady=(6, 8)
        )
        lists = ttk.Frame(frame)
        lists.grid(row=2, column=0, sticky="nsew")
        lists.columnconfigure((0, 1), weight=1, uniform="files")
        lists.rowconfigure(0, weight=1)
        self.make_file_panel(lists, "input", "原始考勤 · input", 0)
        self.make_file_panel(lists, "output", "统计结果 · output", 1)
        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.start_button = ttk.Button(actions, text="开始统计全部", command=self.start)
        self.start_button.pack(side="left")
        refresh_button = ttk.Button(actions, text="刷新列表", command=self.refresh_files)
        refresh_button.pack(side="left", padx=8)
        self.controls.extend([self.start_button, refresh_button])
        ttk.Checkbutton(actions, text="显示处理详情", variable=self.details_visible, command=self.toggle_details).pack(side="right")
        self.status_label = ttk.Label(frame, textvariable=self.status, wraplength=850)
        self.status_label.grid(row=4, column=0, sticky="w", pady=(6, 4))
        progress_frame = ttk.Frame(frame)
        progress_frame.grid(row=5, column=0, sticky="ew")
        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Label(progress_frame, textvariable=self.counts).pack(side="right", padx=(12, 0))
        self.details_frame = ttk.LabelFrame(frame, text="处理详情", padding=8)
        self.details_frame.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        detail_actions = ttk.Frame(self.details_frame)
        detail_actions.pack(fill="x")
        ttk.Button(detail_actions, text="复制详情", command=self.copy_details).pack(side="right")
        self.log_button = ttk.Button(detail_actions, text="打开本次日志", command=lambda: self.open_path(self.log_path), state="disabled")
        self.log_button.pack(side="right", padx=8)
        self.details = ScrolledText(self.details_frame, wrap="word", height=3, font=("Microsoft YaHei UI", 10), state="disabled")
        self.details.pack(fill="both", expand=True, pady=(8, 0))
        self.toggle_details()
        ttk.Label(frame, text=f"规则与公司假共用 config.json · 软件及数据位置：{self.base_dir}", foreground="#666666", wraplength=850).grid(
            row=7, column=0, sticky="w", pady=(10, 0)
        )
        for target in (root, self.trees["input"], self.drop_hint):
            target.drop_target_register(DND_FILES)
            target.dnd_bind("<<Drop>>", self.drop_files)
        self.refresh_files()
        self.poll_id = root.after(100, self.poll)

    def make_file_panel(self, parent: ttk.Frame, kind: str, title: str, column: int) -> None:
        panel = ttk.LabelFrame(parent, text=title, padding=8)
        panel.grid(row=0, column=column, sticky="nsew", padx=(0, 6) if column == 0 else (6, 0))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(panel)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        if kind == "input":
            button = ttk.Button(toolbar, text="添加考勤文件…", command=self.select_files)
            self.controls.append(button)
        else:
            button = ttk.Button(toolbar, text="打开所选", command=lambda: self.open_selected(kind))
        button.pack(side="left")
        remove_button = ttk.Button(toolbar, text="移到回收站", command=lambda: self.remove_files(kind))
        remove_button.pack(side="left", padx=6)
        clear_button = ttk.Button(toolbar, text="清空…", command=lambda: self.remove_files(kind, clear=True))
        clear_button.pack(side="right")
        self.controls.extend([remove_button, clear_button])
        if kind == "input":
            self.drop_hint = tk.Label(panel, text="拖入 .xlsx / .xlsm 原始考勤，复制到 input", background="#eff6ff", foreground="#2255a4", font=("Microsoft YaHei UI", 10), pady=6)
            self.drop_hint.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        else:
            ttk.Label(panel, text="双击打开报表；展开批次可查看其中的文件。", wraplength=420).grid(
                row=1, column=0, columnspan=2, sticky="nw", pady=(0, 8)
            )
        columns = ("state", "modified") if kind == "input" else ("modified",)
        tree = ttk.Treeview(panel, columns=columns, selectmode="extended", height=8)
        tree.heading("#0", text="文件名" if kind == "input" else "批次 / 结果文件")
        tree.column("#0", width=220, minwidth=220, stretch=False)
        tree.heading("modified", text="更新时间")
        tree.column("modified", width=140, minwidth=130, stretch=False)
        if kind == "input":
            tree.heading("state", text="状态")
            tree.column("state", width=85, minwidth=75, stretch=False)
        tree.grid(row=2, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=tree.yview)
        scrollbar.grid(row=2, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)
        horizontal = ttk.Scrollbar(panel, orient="horizontal", command=tree.xview)
        horizontal.grid(row=3, column=0, sticky="ew")
        tree.configure(xscrollcommand=horizontal.set)
        tree.bind("<Double-1>", lambda event: self.open_selected(kind, files_only=True))
        tree.bind("<Delete>", lambda event: self.remove_files(kind))
        self.trees[kind] = tree
        footer = ttk.Frame(panel)
        footer.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        summary = self.input_summary if kind == "input" else self.output_summary
        ttk.Label(footer, textvariable=summary).pack(side="left")
        ttk.Button(footer, text=f"打开 {kind} 文件夹", command=lambda: self.open_path(self.base_dir / kind)).pack(side="right")

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
                    tree.insert(parent, "end", iid=item, text=path.name, values=values, open=item in expanded or path == self.result_dir)
                    self.tree_paths[kind][item] = path
                    if kind == "output" and folder:
                        for child in list_entries(path):
                            insert(child, item)

                entries = list_entries(directory)
                for path in reversed(entries) if kind == "output" else entries:
                    insert(path)
                tree.column("#0", width=name_width)
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
            self.status_label.configure(foreground="#16803b")
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

    def show_error(self, title: str, message: str) -> None:
        self.status.set(title + "，请查看处理详情。")
        self.status_label.configure(foreground="#b42318")
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
        self.log_button.configure(state="disabled")
        self.status_label.configure(foreground="#2255a4")
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
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=max(total, 1), value=completed)
        self.counts.set(f"已完成 {completed}/{total} 个文件" if total else "未完成")
        self.status.set(outcome_message(code, completed, total))
        self.status_label.configure(foreground="#16803b" if code == 0 else "#b42318")
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
