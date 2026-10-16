"""pythonw 入口：导入依赖失败时也能显示启动错误。"""
from datetime import datetime
from pathlib import Path
import os
import sys
import traceback


def main(argv=None):
    try:
        from attendance_analyzer.gui import main as open_workbench
        return open_workbench(argv)
    except Exception as error:
        base = Path(__file__).resolve().parent
        detail = traceback.format_exc()
        message = f"考勤工作台启动失败：{error}\n请检查 Python 依赖、目录写入权限和默认浏览器。"
        try:
            logs = base / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            path = logs / f"启动失败_{datetime.now():%Y%m%d_%H%M%S}.log"
            path.write_text(detail, encoding="utf-8-sig")
            message += f"\n\n完整详情：{path}"
        except OSError:
            message += "\n\n" + detail
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "考勤工作台无法启动", 0x10)
        elif sys.stderr:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
