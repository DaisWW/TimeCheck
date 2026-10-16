"""网页窗口的本地能力；文件操作始终限制在软件的数据目录内。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
from threading import RLock, Thread
import tempfile
import traceback
from uuid import uuid4

from . import cli
from .company_calendar import parse_company_holidays
from .config import AppConfig, RULE_FIELDS
from .config_forms import apply_rule_values, holiday_range_dates, rule_form_values
from .file_manager import copy_input, is_link, list_entries, managed_path, prepare_import, recycle_entries
from .job_runner import outcome_message, run_job


def endpoint(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        try:
            return {"ok": True, "data": method(self, *args, **kwargs)}
        except (OSError, ValueError, RuntimeError) as error:
            return {"ok": False, "error": str(error)}
        except Exception:
            return {"ok": False, "error": "操作遇到意外错误，请复制详情供排查。\n" + traceback.format_exc()}
    return call


def _revision(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _stamp(path: Path):
    return (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None


class WebApi:
    def __init__(self, base_dir: Path):
        self._base = base_dir.resolve()
        self._config_path = self._base / "config.json"
        self._lock = RLock()
        self._busy = False
        self._running = False
        self._closed = False
        self._queue = Queue()
        self._imports = {}
        self._uploads = {}
        self._recycles = {}
        self._log = ""
        self._run_id = ""
        self._phase = "ready"
        self._status = "添加钉钉原始考勤，准备好后开始统计。"
        self._completed = self._total = 0
        self._result = self._log_path = None

    def _directory(self, kind):
        if kind not in ("input", "output"):
            raise ValueError("未知的数据目录。")
        return self._base / kind

    def _path(self, kind, identifier):
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("请选择一个文件或批次。")
        return managed_path(self._directory(kind), self._directory(kind) / identifier)

    def _idle(self):
        if self._closed:
            raise ValueError("程序已退出，请重新双击打开。")
        if self._running or self._busy:
            raise ValueError("正在处理，请等待完成后再操作。")

    @contextmanager
    def _exclusive(self):
        with self._lock:
            self._idle()
            self._busy = True
        try:
            yield
        finally:
            with self._lock:
                self._busy = False

    def _files(self):
        config_error = None
        try:
            config = AppConfig.load(self._config_path)
            eligible = set(cli._find_input_files(self._directory("input"), config))
        except (OSError, ValueError) as error:
            eligible = set()
            config_error = str(error)

        def entries(kind, directory):
            result = []
            paths = list_entries(directory)
            for path in reversed(paths) if kind == "output" else paths:
                link = is_link(path)
                info = path.lstat()
                folder = not link and path.is_dir()
                result.append({
                    "id": path.relative_to(self._directory(kind)).as_posix(), "name": path.name,
                    "folder": folder, "blocked": link, "size": info.st_size,
                    "modified": datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "eligible": path in eligible and not link,
                    "children": entries(kind, path) if kind == "output" and folder else [],
                })
            return result

        return {"input": entries("input", self._directory("input")),
                "output": entries("output", self._directory("output")), "config_error": config_error}

    @endpoint
    def files(self):
        with self._lock:
            return self._files()

    def _prepare(self, paths):
        config = AppConfig.load(self._config_path)
        plans = prepare_import(paths, self._directory("input"), config.report.output_suffix)
        token = uuid4().hex
        self._imports[token] = (plans, [_stamp(target) for _, target in plans])
        return {"token": token, "names": [target.name for _, target in plans],
                "duplicates": [target.name for _, target in plans if target.exists()]}

    @endpoint
    def begin_upload(self, files):
        with self._exclusive():
            if not isinstance(files, list) or not files:
                raise ValueError("请选择考勤文件。")
            names = set()
            for item in files:
                if (not isinstance(item, dict) or set(item) != {"name", "size"}
                        or not isinstance(item["name"], str) or Path(item["name"]).name != item["name"]
                        or any(char in item["name"] for char in '/\\:*?"<>|')
                        or type(item["size"]) is not int or item["size"] < 0):
                    raise ValueError("文件名或大小无效，请重新选择原始文件。")
                if item["name"].casefold() in names:
                    raise ValueError(f"本次文件重名：{item['name']}，请先重命名。本次未导入任何文件。")
                names.add(item["name"].casefold())
            temporary = tempfile.TemporaryDirectory(prefix="attendance_upload_")
            try:
                paths = [Path(temporary.name) / item["name"] for item in files]
                for path in paths:
                    path.touch()
                plan = self._prepare(paths)
                self._uploads[plan["token"]] = (temporary, [item["size"] for item in files], set())
                return plan
            except Exception:
                temporary.cleanup()
                raise

    def _receive_upload(self, token, index, stream, length):
        with self._exclusive():
            if token not in self._uploads or token not in self._imports:
                raise ValueError("文件选择已失效，请重新添加。")
            temporary, sizes, received = self._uploads[token]
            plans, _ = self._imports[token]
            if not 0 <= index < len(sizes) or length != sizes[index]:
                raise ValueError("文件大小不符，请重新添加。")
            source = plans[index][0]
            received.discard(index)
            with source.open("wb") as target:
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("文件传输中断，本次文件未导入，请重试。")
                    target.write(chunk)
                    remaining -= len(chunk)
            received.add(index)

    @endpoint
    def import_files(self, token, choice):
        with self._exclusive():
            if choice not in ("replace", "skip", "cancel"):
                raise ValueError("请选择替换、跳过或取消。")
            if token not in self._imports:
                raise ValueError("导入选择已失效，请重新添加文件。")
            plans, stamps = self._imports[token]
            if choice == "cancel":
                self._discard_upload(token)
                return {"copied": 0, "skipped": len(plans)}
            if token in self._uploads:
                _, sizes, received = self._uploads[token]
                if len(received) != len(plans) or any(source.stat().st_size != size for (source, _), size in zip(plans, sizes)):
                    raise ValueError("文件尚未传输完成，本次未导入，请重试。")
            # 确认期间文件若发生变化，不能覆盖未经确认的新内容。
            if stamps != [_stamp(managed_path(self._directory("input"), target)) for _, target in plans]:
                raise ValueError("input 在确认期间发生了变化，请重新添加并核对同名文件。")
            config = AppConfig.load(self._config_path)
            prepare_import([source for source, _ in plans], self._directory("input"), config.report.output_suffix)
            copied = 0
            try:
                for source, target in plans:
                    if target.exists() and choice == "skip":
                        continue
                    copy_input(source, target, replace=choice == "replace")
                    copied += 1
            except (OSError, ValueError) as error:
                raise OSError(f"{error}\n本次已添加 {copied}/{len(plans)} 个文件，原始位置的文件保留。") from error
            self._discard_upload(token)
            return {"copied": copied, "skipped": len(plans) - copied}

    def _discard_upload(self, token):
        self._imports.pop(token, None)
        upload = self._uploads.pop(token, None)
        if upload:
            upload[0].cleanup()

    @endpoint
    def preview_recycle(self, kind, identifiers, clear=False):
        with self._exclusive():
            if not isinstance(identifiers, list) or type(clear) is not bool:
                raise ValueError("文件选择格式无效。")
            paths = list_entries(self._directory(kind)) if clear else [self._path(kind, item) for item in identifiers]
            paths = sorted(set(paths), key=lambda path: len(path.parts))
            selected = []
            for path in paths:
                managed_path(self._directory(kind), path)
                if not path.exists():
                    raise ValueError(f"文件已不存在，请刷新列表：{path.name}")
                if not any(path.is_relative_to(parent) for parent in selected):
                    selected.append(path)
            if not selected:
                raise ValueError("没有待清理的项目，请先选择文件或批次。")
            token = uuid4().hex
            self._recycles = {token: (kind, selected, [_stamp(path) for path in selected])}
            return {"token": token, "names": [str(path.relative_to(self._directory(kind))) +
                    ("（包含其中全部文件）" if path.is_dir() else "") for path in selected]}

    @endpoint
    def recycle(self, token):
        with self._exclusive():
            if token not in self._recycles:
                raise ValueError("清理确认已失效，请重新选择。")
            kind, paths, stamps = self._recycles.pop(token)
            if stamps != [_stamp(self._path(kind, str(path.relative_to(self._directory(kind))))) for path in paths]:
                raise ValueError("所选内容在确认期间发生了变化，请刷新后重新确认。")
            return recycle_entries(self._directory(kind), paths)

    @endpoint
    def open_path(self, kind, identifier=None):
        if kind in ("input", "output"):
            if identifier is None:
                list_entries(self._directory(kind))
                path = self._directory(kind)
            else:
                path = self._path(kind, identifier)
        elif kind in ("result", "log"):
            path = self._result if kind == "result" else self._log_path
            if path:
                path = managed_path(self._base / ("output" if kind == "result" else "logs"), path)
        else:
            raise ValueError("未知的打开目标。")
        if path is None or not path.exists():
            raise ValueError("文件或目录已不存在，请刷新列表。")
        os.startfile(path)

    def _settings(self, config, kind):
        if kind == "rules":
            return rule_form_values(config)
        if kind == "holidays":
            return config.to_dict()["company_holidays"]
        raise ValueError("未知的配置类型。")

    @endpoint
    def get_settings(self, kind):
        with self._exclusive():
            config = AppConfig.load(self._config_path)
            values = self._settings(config, kind)
            return {"values": values, "revision": _revision(values),
                    "fields": [{"key": f"{section}.{key}", "group": group, "label": label, "help": hint}
                               for section, key, group, label, hint in RULE_FIELDS] if kind == "rules" else []}

    @endpoint
    def save_settings(self, kind, values, revision):
        with self._exclusive():
            config = AppConfig.load(self._config_path)
            if revision != _revision(self._settings(config, kind)):
                raise ValueError("此配置已被另一个窗口或外部程序修改。本次未保存，请保留当前草稿，重新打开后核对。")
            config = apply_rule_values(config, values) if kind == "rules" else replace(
                config, company_holidays=parse_company_holidays(values))
            config.save(self._config_path)
            return self._settings(config, kind)

    @endpoint
    def add_holiday_range(self, rows, start, end, period, note):
        with self._exclusive():
            existing = {item.work_date: item for item in parse_company_holidays(rows)}
            days = holiday_range_dates(start, end)
            conflicts = [day.isoformat() for day in days if day in existing]
            if conflicts:
                raise ValueError("以下日期已有安排：" + "、".join(conflicts) + "。本次未加入任何日期，请调整范围或修改已有安排。")
            values = rows + [{"date": day.isoformat(), "period": period, "note": note} for day in days]
            parse_company_holidays(values)
            return sorted(values, key=lambda row: row["date"])

    @endpoint
    def start(self):
        with self._exclusive():
            config = AppConfig.load(self._config_path)
            inputs = cli._find_input_files(self._directory("input"), config)
            if not inputs:
                raise ValueError("input 中没有可统计的考勤文件，请先添加 .xlsx / .xlsm 原始考勤。")
            for path in inputs:
                managed_path(self._directory("input"), path)
            list_entries(self._directory("output"))
            with self._lock:
                for token in list(self._imports):
                    self._discard_upload(token)
                self._recycles.clear()
                self._run_id = uuid4().hex
                self._result = self._directory("output") / f"统计_{datetime.now():%Y%m%d_%H%M%S_%f}"
                self._log_path = None
                self._log = ""
                self._completed, self._total = 0, len(inputs)
                self._phase, self._status = "running", "正在检查规则与输入，请稍候…"
                self._running = True
            try:
                def run():
                    run_job(self._directory("input"), self._result, self._config_path,
                            self._base / "logs", self._queue)
                    # 即使浏览器已关闭，也要收取完成事件，让空闲监控能退出。
                    self.poll()

                Thread(target=run, daemon=True).start()
            except Exception:
                self._running = False
                raise
            return self._run_id

    @endpoint
    def poll(self, cursor=0, run_id=""):
        with self._lock:
            refresh = False
            for _ in range(2000):
                try:
                    event, *values = self._queue.get_nowait()
                except Empty:
                    break
                if event == "log":
                    self._log += values[0]
                elif event == "progress":
                    self._completed, self._total, self._status = values
                elif event == "finished":
                    code, self._log_path, self._completed, self._total = values
                    self._running = False
                    self._phase = "success" if code == 0 else "error"
                    self._status = outcome_message(code, self._completed, self._total)
                    refresh = True
            if type(cursor) is not int or cursor < 0 or cursor > len(self._log) or run_id != self._run_id:
                cursor = 0
            return {"busy": self._busy or self._running, "running": self._running, "phase": self._phase,
                    "status": self._status, "completed": self._completed, "total": self._total,
                    "run_id": self._run_id, "log": self._log[cursor:], "cursor": len(self._log),
                    "result": bool(self._result and self._result.is_dir()),
                    "log_available": bool(self._log_path and self._log_path.is_file()),
                    "refresh": refresh}

    @endpoint
    def close(self):
        with self._lock:
            if self._closed:
                return
            self._idle()
            self._closed = True
            for token in list(self._imports):
                self._discard_upload(token)
