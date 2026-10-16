from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import io
from pathlib import Path
from queue import Queue
import sys
import traceback

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
    """在工作线程运行；状态与日志通过队列交给工作台服务。"""
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
