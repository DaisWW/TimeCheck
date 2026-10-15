"""窗口中的文件导入和回收站操作，限定在本软件的数据目录内。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import tempfile

from send2trash import send2trash


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def ensure_directory(directory: Path) -> None:
    if directory.is_symlink() or (directory.exists() and is_link(directory)):
        raise ValueError(f"数据目录不能是链接或目录联接：{directory}")
    directory.mkdir(parents=True, exist_ok=True)


def list_entries(directory: Path) -> list[Path]:
    ensure_directory(directory)
    return sorted((path for path in directory.iterdir() if path.name != ".gitkeep"), key=lambda path: path.name.casefold())


def managed_path(directory: Path, path: Path) -> Path:
    directory = Path(os.path.abspath(directory))
    path = Path(os.path.abspath(path))
    if not path.is_relative_to(directory) or path == directory:
        raise ValueError(f"只能管理 {directory} 内的文件，不能操作数据目录本身：{path}")
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (candidate.exists() and is_link(candidate)):
            raise ValueError(f"不能操作链接或目录联接：{candidate}")
        if candidate == directory:
            break
    return path


def prepare_import(sources: list[Path], directory: Path, output_suffix: str) -> list[tuple[Path, Path]]:
    ensure_directory(directory)
    plans = {}
    errors = []
    marker = Path(output_suffix).stem
    for source in sources:
        source = source.expanduser().resolve()
        if not source.is_file():
            errors.append(f"不是可读取的文件：{source}")
        elif source.suffix.lower() not in {".xlsx", ".xlsm"}:
            errors.append(f"不支持的文件格式：{source.name}（请导入 .xlsx / .xlsm）")
        elif source.name.startswith("~$") or marker in source.stem:
            errors.append(f"请导入钉钉原始考勤，不能导入临时文件或分析结果：{source.name}")
        else:
            target = managed_path(directory, directory / source.name)
            if target.is_dir():
                errors.append(f"input 中已有同名文件夹：{target.name}")
            elif source != target:
                key = target.name.casefold()
                if key in plans and plans[key][0] != source:
                    errors.append(f"本次选择的文件重名，请先重命名：{plans[key][0]} 和 {source}")
                else:
                    plans[key] = (source, target)
    if errors:
        raise ValueError("本次尚未导入任何文件，请修正以下问题：\n" + "\n".join(errors))
    return list(plans.values())


def copy_input(source: Path, target: Path, *, replace: bool = False) -> None:
    target = managed_path(target.parent, target)
    if target.exists() and not replace:
        raise FileExistsError(f"input 中已有同名文件：{target.name}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".attendance_import_", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
        # 仅复制内容，避免继承源文件的只读属性，导致 HR 无法清理副本。
        shutil.copyfile(source, temporary)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def recycle_entries(directory: Path, paths: list[Path]) -> int:
    paths = sorted({managed_path(directory, path) for path in paths}, key=lambda path: len(path.parts))
    selected = []
    for path in paths:
        if any(path.is_relative_to(parent) for parent in selected):
            continue
        selected.append(path)
    completed = 0
    for path in selected:
        try:
            send2trash(str(path))
        except OSError as error:
            raise OSError(f"无法移到回收站：{path}\n{error}\n已移走 {completed}/{len(selected)} 项，其余保留。请关闭占用文件后重试。") from error
        completed += 1
    return completed
