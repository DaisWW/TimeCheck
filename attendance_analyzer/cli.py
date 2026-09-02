from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .analyzer import AttendanceAnalyzer
from .config import AppConfig, find_default_config
from .report_writer import OvertimeReportWriter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="分析钉钉考勤导出 Excel 中的加班情况。")
    parser.add_argument(
        "input",
        nargs="?",
        help="钉钉考勤 Excel 或输入目录。未指定时批量分析 ./input。",
    )
    parser.add_argument("-c", "--config", help="TOML 配置文件路径。未指定时优先读取 ./config.toml。")
    parser.add_argument(
        "-o",
        "--output",
        help="单文件模式下为输出文件；批量模式下为输出目录。默认写入 ./output。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = find_default_config(args.config)
    config = AppConfig.load(config_path)
    config.validate()

    if args.input:
        input_path = Path(args.input).expanduser().resolve()
        if not input_path.exists():
            print(f"找不到输入文件或目录：{input_path}", file=sys.stderr)
            return 2
        if input_path.is_file():
            output_path = (
                Path(args.output).expanduser().resolve()
                if args.output
                else _default_output_path(input_path, config)
            )
            return _analyze_file(input_path, output_path, config)
        input_dir = input_path
    else:
        input_dir = _base_dir() / "input"

    output_dir = Path(args.output).expanduser().resolve() if args.output else _base_dir() / "output"
    return _analyze_directory(input_dir, output_dir, config)


def _analyze_directory(input_dir: Path, output_dir: Path, config: AppConfig) -> int:
    if not input_dir.is_dir():
        print(f"找不到输入目录：{input_dir}", file=sys.stderr)
        return 2

    input_paths = _find_input_files(input_dir, config)
    if not input_paths:
        print(f"输入目录中没有可分析的 Excel：{input_dir}", file=sys.stderr)
        return 2

    for input_path in input_paths:
        output_path = output_dir / f"{input_path.stem}{config.report.output_suffix}"
        code = _analyze_file(input_path, output_path, config)
        if code != 0:
            return code

    print(f"批量分析完成：{len(input_paths)} 个文件")
    print(f"输出目录：{output_dir}")
    return 0


def _analyze_file(input_path: Path, output_path: Path, config: AppConfig) -> int:
    analyzer = AttendanceAnalyzer(config)
    results = analyzer.analyze_overtime(input_path)
    try:
        OvertimeReportWriter(config).write(output_path, results, analyzer.last_input_issues)
    except PermissionError:
        print(f"无法写入输出文件：{output_path}", file=sys.stderr)
        print("可能原因：文件正被 Excel 打开，或当前用户没有写入权限。", file=sys.stderr)
        print("请关闭该文件后重试，或使用 -o 指定新的输出路径。", file=sys.stderr)
        return 3

    overtime_days = sum(1 for item in results if item.has_overtime)
    overtime_minutes = sum(item.overtime_minutes for item in results)
    late_minutes = sum(item.late_minutes for item in results)
    late_chargeable_minutes = sum(item.late_chargeable_minutes for item in results)
    print(f"输入文件：{input_path}")
    print(f"已分析记录数：{len(results)}")
    print(f"加班天数：{overtime_days}")
    print(f"加班小时：{round(overtime_minutes / 60, 2)}")
    print(f"迟到分钟：{late_minutes}")
    print(f"扣款迟到分钟：{late_chargeable_minutes}")
    print(f"待人工确认输入：{len(analyzer.last_input_issues)} 条")
    print(f"输出文件：{output_path}")
    return 0


def _default_output_path(input_path: Path, config: AppConfig) -> Path:
    return _base_dir() / "output" / f"{input_path.stem}{config.report.output_suffix}"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def _find_input_files(input_dir: Path, config: AppConfig) -> list[Path]:
    output_marker = Path(config.report.output_suffix).stem
    return sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in {".xlsx", ".xlsm"}
        and not path.name.startswith("~$")
        and output_marker not in path.stem
    )


if __name__ == "__main__":
    raise SystemExit(main())
