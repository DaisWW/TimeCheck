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
                f"{root.name}.exe", "config.csv", "公司放假.csv", "使用说明.txt",
                "配置模板/config.csv", "配置模板/公司放假.csv",
            }
            for filename in required | {"input/真实考勤.xlsx", "output/工资.xlsx", "logs/个人数据.log", "config.toml"}:
                path = root / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"placeholder")
            destination = create_release_zip(root)
            self.assertEqual(destination, root.with_suffix(".zip"))
            with ZipFile(destination) as archive:
                self.assertIsNone(archive.testzip())
                expected = required | {"input/", "output/", "logs/"}
                self.assertEqual(set(archive.namelist()), {f"{root.name}/{item}" for item in expected})

    def test_missing_release_file_preserves_previous_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "release"
            root.mkdir()
            destination = root.with_suffix(".zip")
            destination.write_bytes(b"previous release")
            with self.assertRaises(FileNotFoundError):
                create_release_zip(root)
            self.assertEqual(destination.read_bytes(), b"previous release")


if __name__ == "__main__":
    unittest.main()
