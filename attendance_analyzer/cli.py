from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
import sys
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

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
    parser.add_argument("-c", "--config", help="JSON 配置文件路径。未指定时读取当前目录的 config.json。")
    parser.add_argument(
        "-o",
        "--output",
        help="单文件模式下为输出文件；批量模式下为输出目录。默认写入 ./output。",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    output_dir: Path | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    config_path = find_default_config(args.config)
    try:
        config = AppConfig.load(config_path)
        config.validate()
    except (OSError, ValueError) as error:
        print(f"配置错误（{config_path}）：{error}", file=sys.stderr)
        return 2
    print(f"使用配置：{config_path.resolve()}" if config_path else "使用内置默认配置")
    print(f"公司放假安排：{len(config.company_holidays)} 天（保存在同一份配置中）")

    if args.input:
        input_path = Path(args.input).expanduser().resolve()
        if not input_path.exists():
            print(f"找不到输入文件或目录：{input_path}", file=sys.stderr)
            return 2
        if input_path.is_file():
            output_path = (
                Path(args.output).expanduser().resolve()
                if args.output
                else (output_dir / f"{input_path.stem}{config.report.output_suffix}" if output_dir else _default_output_path(input_path, config))
            )
            code = _analyze_file(
                input_path, output_path, config,
                progress=(lambda status: progress(0, 1, status)) if progress else None,
            )
            if code == 0 and progress:
                progress(1, 1, "统计完成")
            return code
        input_dir = input_path
    else:
        input_dir = Path.cwd().resolve() / "input"

    output_dir = Path(args.output).expanduser().resolve() if args.output else (output_dir or Path.cwd().resolve() / "output")
    return _analyze_directory(input_dir, output_dir, config, progress=progress)


def _analyze_directory(
    input_dir: Path,
    output_dir: Path,
    config: AppConfig,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> int:
    if not input_dir.is_dir():
        print(f"找不到输入目录：{input_dir}", file=sys.stderr)
        return 2

    input_paths = _find_input_files(input_dir, config)
    if not input_paths:
        print(f"输入目录中没有可分析的 Excel：{input_dir}", file=sys.stderr)
        return 2

    report_sources: dict[Path, Path] = {}
    for input_path in input_paths:
        output_path = output_dir / f"{input_path.stem}{config.report.output_suffix}"
        if output_path in report_sources:
            print(
                f"输出文件重名：{report_sources[output_path].name} 与 {input_path.name} 将写入同一份结果 {output_path}。\n"
                "请先重命名其中一个输入文件，再重新统计。",
                file=sys.stderr,
            )
            return 2
        report_sources[output_path] = input_path

    for index, input_path in enumerate(input_paths, 1):
        print(f"正在处理文件 {index}/{len(input_paths)}：{input_path.name}")
        output_path = output_dir / f"{input_path.stem}{config.report.output_suffix}"
        code = _analyze_file(
            input_path, output_path, config,
            progress=(lambda status: progress(index - 1, len(input_paths), status)) if progress else None,
        )
        if code != 0:
            return code
        if progress:
            progress(index, len(input_paths), f"已完成：{input_path.name}")

    print(f"批量分析完成：{len(input_paths)} 个文件")
    print(f"输出目录：{output_dir}")
    return 0


def _analyze_file(
    input_path: Path,
    output_path: Path,
    config: AppConfig,
    *,
    progress: Callable[[str], None] | None = None,
) -> int:
    if input_path.suffix.lower() not in {".xlsx", ".xlsm"}:
        print(f"不支持的输入格式：{input_path}；请使用 .xlsx 或 .xlsm。", file=sys.stderr)
        return 4
    if input_path.resolve() == output_path.resolve():
        print("输出路径不能覆盖输入考勤文件。", file=sys.stderr)
        return 2
    analyzer = AttendanceAnalyzer(config)
    if progress:
        progress(f"读取和计算：{input_path.name}")
    print(f"正在读取和计算：{input_path.name}")
    try:
        results = analyzer.analyze_overtime(input_path)
    except (ValueError, OSError, BadZipFile, InvalidFileException) as error:
        print(f"考勤文件处理失败：{input_path}\n{error}", file=sys.stderr)
        if output_path.exists():
            print(f"本次结果未更新，已有文件仍是旧结果：{output_path}", file=sys.stderr)
        return 4
    if progress:
        progress(f"生成报表：{input_path.name}")
    print(f"正在生成报表：{output_path.name}")
    try:
        OvertimeReportWriter(config).write(output_path, results, analyzer.last_input_issues)
    except PermissionError:
        print(f"无法写入输出文件：{output_path}", file=sys.stderr)
        print("可能原因：文件正被 Excel 打开，或当前用户没有写入权限。", file=sys.stderr)
        print("请关闭该文件后重试，或使用 -o 指定新的输出路径。", file=sys.stderr)
        return 3
    except OSError as error:
        print(f"无法保存结果：{output_path}\n{error}", file=sys.stderr)
        print("请检查目标目录的写入权限和剩余空间，修正后重试。", file=sys.stderr)
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
    return Path.cwd().resolve() / "output" / f"{input_path.stem}{config.report.output_suffix}"


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
