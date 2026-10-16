import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from scripts.package_release import PYTHON_ARCHIVE, RUNTIME_FILES, create_release_zip, download_python


def make_runtime(_source, runtime):
    for filename in RUNTIME_FILES + ("Lib/site-packages/pywin32.pth",):
        path = runtime / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"bundled runtime fixture")


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
    @patch("scripts.package_release.prepare_runtime", side_effect=make_runtime)
    def test_release_zip_contains_only_program_runtime_configuration_and_empty_data_directories(self, _prepare):
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
                expected = (set(required.values()) | {"input/", "output/", "logs/"}
                            | {f"runtime/{name}" for name in RUNTIME_FILES + ("Lib/site-packages/pywin32.pth",)})
                self.assertEqual(set(archive.namelist()), {f"dingding-attendance/{item}" for item in expected})
                for source, target in required.items():
                    self.assertEqual(archive.read(f"dingding-attendance/{target}"), (root / source).read_bytes())
                for filename in RUNTIME_FILES:
                    self.assertEqual(archive.read(f"dingding-attendance/runtime/{filename}"), b"bundled runtime fixture")
            self.assertEqual(list((root / "build").iterdir()), [destination])

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

    def test_incomplete_runtime_preserves_previous_zip_and_removes_staging(self):
        for filename in ("pythonw.exe", "Lib/site-packages/pywin32_system32/pywintypes313.dll"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                make_source(root)
                destination = root / "build/dingding-attendance.zip"
                destination.parent.mkdir()
                destination.write_bytes(b"previous release")

                def incomplete(source, runtime):
                    make_runtime(source, runtime)
                    (runtime / filename).unlink()

                with patch("scripts.package_release.prepare_runtime", side_effect=incomplete):
                    with self.assertRaisesRegex(FileNotFoundError, filename):
                        create_release_zip(root)
                self.assertEqual(destination.read_bytes(), b"previous release")
                self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_runtime_preparation_or_zip_verification_failure_preserves_previous_zip(self):
        for stage in ("runtime", "zip"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                make_source(root)
                destination = root / "build/dingding-attendance.zip"
                destination.parent.mkdir()
                destination.write_bytes(b"previous release")
                with patch("scripts.package_release.prepare_runtime", side_effect=(OSError("download failed") if stage == "runtime" else make_runtime)), \
                        patch("scripts.package_release.ZipFile.testzip", return_value="runtime/python.exe"):
                    with self.assertRaises((OSError, ValueError)):
                        create_release_zip(root)
                self.assertEqual(destination.read_bytes(), b"previous release")
                self.assertEqual(list(destination.parent.iterdir()), [destination])


class RuntimeDownloadTest(unittest.TestCase):
    def test_verified_download_is_cached_and_reused_without_network(self):
        content = b"official Python fixture"
        with tempfile.TemporaryDirectory() as directory, \
                patch("scripts.package_release.PYTHON_SHA256", hashlib.sha256(content).hexdigest()), \
                patch("scripts.package_release.urlopen", return_value=io.BytesIO(content)) as request:
            root = Path(directory)
            archive = download_python(root)
            self.assertEqual(archive.read_bytes(), content)
            self.assertEqual(download_python(root), archive)
            request.assert_called_once()
            self.assertTrue(request.call_args.args[0].full_url.endswith(PYTHON_ARCHIVE))
            self.assertFalse(archive.with_suffix(".zip.partial").exists())

    def test_corrupt_download_is_not_cached(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("scripts.package_release.urlopen", return_value=io.BytesIO(b"damaged download")):
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                download_python(root)
            self.assertEqual(list((root / "runtime-cache").iterdir()), [])

    def test_corrupt_cache_is_rejected_before_use(self):
        with tempfile.TemporaryDirectory() as directory, patch("scripts.package_release.urlopen") as request:
            root = Path(directory)
            cache = root / "runtime-cache"
            cache.mkdir()
            (cache / PYTHON_ARCHIVE).write_bytes(b"damaged cache")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                download_python(root)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
