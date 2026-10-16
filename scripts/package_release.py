"""生成 Windows x64 免安装 ZIP，内置 Python 和依赖，排除本机考勤数据。"""

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile


# 固定官方嵌入版及发布页 SHA-256，升级时一起核对并重新验收。
# https://www.python.org/downloads/release/python-31316/
PYTHON_VERSION = "3.13.16"
PYTHON_SHA256 = "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"
PYTHON_ARCHIVE = f"python-{PYTHON_VERSION}-embed-amd64.zip"
RUNTIME_FILES = (
    "python.exe", "pythonw.exe", "python313.dll", "python313.zip", "python313._pth",
    "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt",
    "Lib/site-packages/openpyxl/__init__.py", "Lib/site-packages/send2trash/__init__.py",
    "Lib/site-packages/lxml/__init__.py",
    "Lib/site-packages/pywin32_system32/pythoncom313.dll",
    "Lib/site-packages/pywin32_system32/pywintypes313.dll",
)


def download_python(build_dir: Path) -> Path:
    cache = build_dir / "runtime-cache"
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / PYTHON_ARCHIVE
    if destination.is_file():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != PYTHON_SHA256:
            raise ValueError(f"Python 缓存 SHA-256 校验失败，请删除后重试：{destination}")
        return destination

    temporary = destination.with_suffix(".zip.partial")
    try:
        url = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_ARCHIVE}"
        print(f"下载官方便携 Python {PYTHON_VERSION}…", flush=True)
        with urlopen(Request(url, headers={"Accept-Encoding": "identity"}), timeout=60) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != PYTHON_SHA256:
            raise ValueError("下载的 Python SHA-256 校验失败，未生成新发布包。")
        temporary.write_bytes(content)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def prepare_runtime(source_dir: Path, runtime_dir: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("免安装包须在 Windows 64 位电脑上构建并验证。")
    archive_path = download_python(source_dir / "build")
    with ZipFile(archive_path) as archive:
        failed = archive.testzip()
        if failed:
            raise ValueError(f"Python ZIP 完整性校验失败：{failed}")
        archive.extractall(runtime_dir)

    # _pth 隔离系统环境；启用 site 以加载 pywin32 的 DLL 引导 .pth。
    (runtime_dir / "python313._pth").write_text(
        "python313.zip\n.\n..\nLib/site-packages\nimport site\n", encoding="utf-8"
    )
    print("准备包内依赖（仅构建机需要联网）…", flush=True)
    subprocess.run([
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-conflicts",
        "--only-binary=:all:", "--no-compile", "--platform", "win_amd64",
        "--python-version", "3.13", "--implementation", "cp", "--abi", "cp313",
        "--target", str(runtime_dir / "Lib/site-packages"),
        "-r", str(source_dir / "requirements.txt"),
    ], check=True)

    # 只保留 Windows 系统 PATH，实际加载包内 Excel / COM / 回收站组件。
    environment = os.environ.copy()
    environment["PATH"] = str(Path(environment["SYSTEMROOT"]) / "System32")
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    subprocess.run([
        str(runtime_dir / "python.exe"), "-s", "-B", "-X", "utf8", "-c", """
import pathlib, site, sys
assert sys.version_info[:3] == (3, 13, 16)
assert sys.flags.isolated and site.ENABLE_USER_SITE is False
root = pathlib.Path(sys.executable).resolve().parent.parent
assert all(pathlib.Path(p).resolve().is_relative_to(root) for p in sys.path)
import openpyxl, pythoncom, pywintypes
assert openpyxl.LXML, 'Excel 快速写出组件未加载'
from win32com.shell import shell
from send2trash import send2trash
pythoncom.CoInitialize()
pythoncom.CoUninitialize()
sys.path.insert(0, sys.argv[1])
import attendance_analyzer.gui, attendance_analyzer.cli
print('包内 Python、Excel 加速、COM 和回收站依赖验证通过。')
""", str(source_dir),
    ], cwd=runtime_dir.parent, env=environment, check=True)


def create_release_zip(source_dir: Path) -> Path:
    source_dir = source_dir.resolve()
    files = {
        "main.py": "main.py",
        "启动考勤工作台.vbs": "启动考勤工作台.vbs",
        "一键统计考勤.bat": "一键统计考勤.bat",
        "requirements.txt": "requirements.txt",
        "config.json": "config.json",
        "config.example.json": "配置模板/config.json",
        "docs/HR使用说明.txt": "使用说明.txt",
    }
    for name in (
        "__init__", "analyzer", "approval", "cli", "company_calendar", "config",
        "config_forms", "excel_reader", "file_manager", "gui", "job_runner",
        "models", "report_writer", "rules", "time_utils", "web_api",
    ):
        relative = f"attendance_analyzer/{name}.py"
        files[relative] = relative
    for name in ("index.html", "app.css", "app.js"):
        relative = f"attendance_analyzer/web/{name}"
        files[relative] = relative
    for filename in files:
        if not (source_dir / filename).is_file():
            raise FileNotFoundError(f"发布文件缺失：{source_dir / filename}")

    destination = source_dir / "build/dingding-attendance.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".zip.partial")
    try:
        with tempfile.TemporaryDirectory(prefix="portable-", dir=destination.parent) as staging:
            runtime = Path(staging) / "runtime"
            prepare_runtime(source_dir, runtime)
            for filename in RUNTIME_FILES:
                if not (runtime / filename).is_file():
                    raise FileNotFoundError(f"包内运行环境文件缺失：runtime/{filename}")
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
                for filename, target in files.items():
                    archive.write(source_dir / filename, f"dingding-attendance/{target}")
                for path in sorted(runtime.rglob("*")):
                    if path.is_file():
                        archive.write(path, f"dingding-attendance/runtime/{path.relative_to(runtime).as_posix()}")
                for directory in ("input", "output", "logs"):
                    archive.writestr(f"dingding-attendance/{directory}/", "")
        with ZipFile(temporary) as archive:
            failed = archive.testzip()
            if failed:
                raise ValueError(f"ZIP 完整性校验失败：{failed}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path, nargs="?", default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(create_release_zip(args.source_dir))
