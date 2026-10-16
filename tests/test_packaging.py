from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from scripts.package_release import create_release_zip


def make_source(root):
    repository = Path(__file__).resolve().parents[1]
    required = {
        "main.py": "main.py",
        "启动考勤工作台.vbs": "启动考勤工作台.vbs",
        "一键统计考勤.bat": "一键统计考勤.bat",
        "requirements.txt": "requirements.txt",
        "attendance_analyzer/web/index.html": "attendance_analyzer/web/index.html",
        "attendance_analyzer/web/app.css": "attendance_analyzer/web/app.css",
        "attendance_analyzer/web/app.js": "attendance_analyzer/web/app.js",
        "config.json": "config.json",
        "config.example.json": "配置模板/config.json",
        "docs/HR使用说明.txt": "使用说明.txt",
    }
    for path in (repository / "attendance_analyzer").glob("*.py"):
        relative = path.relative_to(repository).as_posix()
        required[relative] = relative
    for filename in required:
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((repository / filename).read_bytes())
    return required


class PackagingTest(unittest.TestCase):
    def test_release_zip_contains_only_program_configuration_and_empty_data_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "中文 发布目录"
            required = make_source(root)
            excluded = {"input/真实考勤.xlsx", "output/工资.xlsx", "logs/个人数据.log", "config.toml", "config.csv", "公司放假.csv", "window_paths.json", "old.exe", "attendance_analyzer/local_debug.py", "attendance_analyzer/__pycache__/old.py", "attendance_analyzer/web/secret.txt"}
            for filename in excluded:
                path = root / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"placeholder")
            destination = create_release_zip(root)
            self.assertEqual(destination, root / "build/dingding-attendance.zip")
            with ZipFile(destination) as archive:
                self.assertIsNone(archive.testzip())
                expected = set(required.values()) | {"input/", "output/", "logs/"}
                self.assertEqual(set(archive.namelist()), {f"dingding-attendance/{item}" for item in expected})
                for source, target in required.items():
                    self.assertEqual(archive.read(f"dingding-attendance/{target}"), (root / source).read_bytes())

    def test_missing_release_file_preserves_previous_zip(self):
        for filename in ("main.py", "attendance_analyzer/gui.py", "attendance_analyzer/rules.py"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "release"
                make_source(root)
                (root / filename).unlink()
                destination = root / "build/dingding-attendance.zip"
                destination.parent.mkdir()
                destination.write_bytes(b"previous release")
                with self.assertRaises(FileNotFoundError) as raised:
                    create_release_zip(root)
                self.assertIn(str(root / filename), str(raised.exception))
                self.assertEqual(destination.read_bytes(), b"previous release")


if __name__ == "__main__":
    unittest.main()
