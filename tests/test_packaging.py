from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from scripts.package_release import create_release_zip


class PackagingTest(unittest.TestCase):
    def test_release_zip_contains_only_program_configuration_and_empty_data_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "中文 发布目录"
            root.mkdir()
            required = {
                "main.py": "main.py",
                "启动考勤工作台.vbs": "启动考勤工作台.vbs",
                "一键统计考勤.bat": "一键统计考勤.bat",
                "requirements.txt": "requirements.txt",
                "attendance_analyzer/__init__.py": "attendance_analyzer/__init__.py",
                "attendance_analyzer/cli.py": "attendance_analyzer/cli.py",
                "attendance_analyzer/web/index.html": "attendance_analyzer/web/index.html",
                "attendance_analyzer/web/app.css": "attendance_analyzer/web/app.css",
                "attendance_analyzer/web/app.js": "attendance_analyzer/web/app.js",
                "config.json": "config.json",
                "config.example.json": "配置模板/config.json",
                "docs/HR使用说明.txt": "使用说明.txt",
            }
            excluded = {"input/真实考勤.xlsx", "output/工资.xlsx", "logs/个人数据.log", "config.toml", "config.csv", "公司放假.csv", "window_paths.json", "old.exe", "attendance_analyzer/__pycache__/old.py", "attendance_analyzer/web/secret.txt"}
            for filename in set(required) | excluded:
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "release"
            root.mkdir()
            destination = root / "build/dingding-attendance.zip"
            destination.parent.mkdir()
            destination.write_bytes(b"previous release")
            with self.assertRaises(FileNotFoundError):
                create_release_zip(root)
            self.assertEqual(destination.read_bytes(), b"previous release")


if __name__ == "__main__":
    unittest.main()
