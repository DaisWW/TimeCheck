from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import io
import os
from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Thread
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import cli


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
    def __init__(self, root: tk.Tk, base_dir: Path, args: argparse.Namespace):
        self.root = root
        self.base_dir = base_dir
        self.config_path = Path(args.config).expanduser().resolve() if args.config else base_dir / "config.csv"
        self.events = Queue()
        self.running = False
        self.log_path = None
        self.result_dir = None
        self.input_value = tk.StringVar(value=args.input or str(base_dir / "input"))
        self.output_value = tk.StringVar(value=args.output or str(base_dir / "output"))
        self.status = tk.StringVar(value="就绪：请选择考勤文件或目录，然后开始统计。")
        self.counts = tk.StringVar(value="")
        root.title("钉钉考勤统计")
        root.geometry("960x700")
        root.minsize(800, 600)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 6))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(9, weight=1)
        ttk.Label(frame, text="考勤统计", style="Title.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(frame, text="选择钉钉导出的 Excel 或目录，检查规则后开始统计。").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(5, 18)
        )
        ttk.Label(frame, text="考勤文件 / 目录").grid(row=2, column=0, sticky="w", padx=(0, 12))
        input_entry = ttk.Entry(frame, textvariable=self.input_value)
        input_entry.grid(row=2, column=1, sticky="ew")
        file_button = ttk.Button(frame, text="选择文件", command=self.select_file)
        file_button.grid(row=2, column=2, padx=(8, 0))
        directory_button = ttk.Button(frame, text="选择目录", command=self.select_input_directory)
        directory_button.grid(row=2, column=3, padx=(8, 0))
        ttk.Label(frame, text="结果保存位置").grid(row=3, column=0, sticky="w", pady=10)
        output_entry = ttk.Entry(frame, textvariable=self.output_value)
        output_entry.grid(row=3, column=1, columnspan=2, sticky="ew")
        output_button = ttk.Button(frame, text="选择目录", command=self.select_output_directory)
        output_button.grid(row=3, column=3, padx=(8, 0))
        ttk.Label(frame, text="每次统计新建一个带时间的结果目录，避免与旧结果混淆。").grid(
            row=4, column=1, columnspan=3, sticky="w"
        )
        config_frame = ttk.LabelFrame(frame, text="规则配置：保存 CSV 后再开始统计", padding=10)
        config_frame.grid(row=5, column=0, columnspan=4, sticky="ew", pady=16)
        config_frame.columnconfigure(0, weight=1)
        ttk.Label(config_frame, text=str(self.config_path), wraplength=500).grid(row=0, column=0, sticky="w")
        rules_button = ttk.Button(config_frame, text="编辑考勤规则", command=lambda: self.open_path(self.config_path))
        rules_button.grid(row=0, column=1, padx=(10, 0))
        holidays_button = ttk.Button(
            config_frame, text="编辑公司放假", command=lambda: self.open_path(self.config_path.parent / "公司放假.csv")
        )
        holidays_button.grid(row=0, column=2, padx=(8, 0))
        actions = ttk.Frame(frame)
        actions.grid(row=6, column=0, columnspan=4, sticky="ew")
        self.start_button = ttk.Button(actions, text="开始统计", command=self.start)
        self.start_button.pack(side="left")
        self.result_button = ttk.Button(actions, text="打开本次结果", command=lambda: self.open_path(self.result_dir), state="disabled")
        self.result_button.pack(side="left", padx=8)
        self.log_button = ttk.Button(actions, text="打开本次日志", command=lambda: self.open_path(self.log_path), state="disabled")
        self.log_button.pack(side="left")
        ttk.Button(actions, text="复制详情", command=self.copy_details).pack(side="right")
        self.controls = [input_entry, output_entry, file_button, directory_button, output_button, rules_button, holidays_button, self.start_button]
        self.status_label = ttk.Label(frame, textvariable=self.status, wraplength=850)
        self.status_label.grid(row=7, column=0, columnspan=4, sticky="w", pady=(14, 8))
        progress_frame = ttk.Frame(frame)
        progress_frame.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(0, 12))
        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Label(progress_frame, textvariable=self.counts).pack(side="right", padx=(12, 0))
        self.details = ScrolledText(frame, wrap="word", height=10, font=("Microsoft YaHei UI", 10), state="disabled")
        self.details.grid(row=9, column=0, columnspan=4, sticky="nsew")
        ttk.Label(frame, text="输入或配置出错时停止当前批次。完整原因会保留在下方详情和本地日志中。").grid(
            row=10, column=0, columnspan=4, sticky="w", pady=(10, 0)
        )
        root.after(100, self.poll)

    def select_file(self) -> None:
        path = filedialog.askopenfilename(title="选择钉钉考勤文件", filetypes=[("Excel 文件", "*.xlsx *.xlsm")])
        if path:
            self.input_value.set(path)

    def select_input_directory(self) -> None:
        path = filedialog.askdirectory(title="选择考勤文件所在目录")
        if path:
            self.input_value.set(path)

    def select_output_directory(self) -> None:
        path = filedialog.askdirectory(title="选择结果保存位置")
        if path:
            self.output_value.set(path)

    def start(self) -> None:
        if self.running:
            return
        if not self.input_value.get().strip() or not self.output_value.get().strip():
            messagebox.showerror("请先选择路径", "请填写考勤文件或目录，以及结果保存位置。", parent=self.root)
            return
        try:
            input_path = Path(self.input_value.get().strip()).expanduser().resolve()
            output_root = Path(self.output_value.get().strip()).expanduser().resolve()
        except (OSError, ValueError) as error:
            messagebox.showerror("路径无效", str(error), parent=self.root)
            return
        self.result_dir = output_root / f"统计_{datetime.now():%Y%m%d_%H%M%S_%f}"
        self.log_path = None
        self.running = True
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.configure(state="disabled")
        for control in self.controls:
            control.configure(state="disabled")
        self.result_button.configure(state="disabled")
        self.log_button.configure(state="disabled")
        self.status_label.configure(foreground="#2255a4")
        self.status.set("正在检查规则与输入，请稍候……")
        self.counts.set("准备中")
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)
        Thread(target=run_job, args=(input_path, self.result_dir, self.config_path, self.base_dir / "logs", self.events), daemon=True).start()

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
        self.root.after(100, self.poll)

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
        if completed:
            self.result_button.configure(state="normal")
        if log_path and log_path.is_file():
            self.log_button.configure(state="normal")
        if code:
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
            self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="钉钉考勤统计窗口")
    parser.add_argument("input", nargs="?", help="预先选择考勤文件或目录")
    parser.add_argument("-c", "--config", help="CSV 基础配置路径")
    parser.add_argument("-o", "--output", help="结果保存目录")
    args = parser.parse_args(argv)
    base_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    root = tk.Tk()
    AttendanceWindow(root, base_dir, args)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
