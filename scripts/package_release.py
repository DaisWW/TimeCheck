"""只将发布所需文件写入 ZIP，避免夹带本地考勤、结果或日志。"""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def create_release_zip(release_dir: Path) -> Path:
    release_dir = release_dir.resolve()
    files = [
        f"{release_dir.name}.exe",
        "config.csv",
        "公司放假.csv",
        "使用说明.txt",
        "配置模板/config.csv",
        "配置模板/公司放假.csv",
    ]
    for filename in files:
        if not (release_dir / filename).is_file():
            raise FileNotFoundError(f"发布文件缺失：{release_dir / filename}")

    destination = release_dir.parent / f"{release_dir.name}.zip"
    temporary = destination.with_suffix(".zip.partial")
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for filename in files:
                archive.write(release_dir / filename, f"{release_dir.name}/{filename}")
            for directory in ("input", "output", "logs"):
                archive.writestr(f"{release_dir.name}/{directory}/", "")
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
    parser.add_argument("release_dir", type=Path)
    args = parser.parse_args()
    print(create_release_zip(args.release_dir))
