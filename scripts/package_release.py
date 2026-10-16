"""生成可直接用 Python 启动的源码 ZIP，排除本机考勤、结果和日志。"""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


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
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for filename, target in files.items():
                archive.write(source_dir / filename, f"dingding-attendance/{target}")
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
