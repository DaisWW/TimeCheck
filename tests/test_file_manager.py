from pathlib import Path
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from attendance_analyzer.file_manager import copy_input, list_entries, managed_path, prepare_import, recycle_entries


class FileManagerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "钉钉 原始文件"
        self.source.mkdir()
        self.input = self.root / "input"

    def file(self, name="甲部门 考勤.xlsx", content=b"original"):
        path = self.source / name
        path.write_bytes(content)
        return path

    def test_import_plans_support_unicode_spaces_deduplicate_and_skip_files_already_in_input(self):
        first, second = self.file(), self.file("乙部门.xlsm")
        plans = prepare_import([first, first, second], self.input, "_加班分析.xlsx")
        self.assertEqual(plans, [(first, self.input / first.name), (second, self.input / second.name)])
        self.assertEqual(list_entries(self.input), [])
        for source, target in plans:
            copy_input(source, target)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertTrue(source.exists())
        self.assertEqual(prepare_import([plans[0][1]], self.input, "_加班分析.xlsx"), [])

    def test_bad_files_and_ambiguous_names_reject_whole_selection_before_copying(self):
        good = self.file()
        for bad in (self.file("旧版.xls"), self.file("说明.txt"), self.file("~$临时.xlsx"), self.file("历史_加班分析.xlsx"), self.source, self.source / "不存在.xlsx"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "尚未导入任何文件"):
                    prepare_import([good, bad], self.input, "_加班分析.xlsx")
                self.assertEqual(list_entries(self.input), [])
        other = self.root / "另一处"
        other.mkdir()
        duplicate = other / good.name
        duplicate.write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "重名"):
            prepare_import([good, duplicate], self.input, "_加班分析.xlsx")

    def test_copy_requires_explicit_replace_and_failed_copy_keeps_existing_file(self):
        source = self.file()
        target = prepare_import([source], self.input, "_加班分析.xlsx")[0][1]
        target.write_bytes(b"previous")
        with self.assertRaises(FileExistsError):
            copy_input(source, target)
        with patch("attendance_analyzer.file_manager.shutil.copyfile", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                copy_input(source, target, replace=True)
        self.assertEqual(target.read_bytes(), b"previous")
        self.assertEqual(list(self.input.glob("*.tmp")), [])
        copy_input(source, target, replace=True)
        self.assertEqual(target.read_bytes(), b"original")

    def test_readonly_source_is_preserved_but_imported_copy_is_writable(self):
        source = self.file()
        target = prepare_import([source], self.input, "_加班分析.xlsx")[0][1]
        source.chmod(stat.S_IREAD)
        try:
            copy_input(source, target)
            self.assertTrue(target.stat().st_mode & stat.S_IWRITE)
            target.unlink()
            self.assertEqual(source.read_bytes(), b"original")
        finally:
            source.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_management_excludes_placeholder_and_rejects_outside_or_root_paths(self):
        list_entries(self.input)
        (self.input / ".gitkeep").touch()
        self.assertEqual(list_entries(self.input), [])
        with patch("attendance_analyzer.file_manager.send2trash") as recycle:
            for path in (self.input, self.file(), self.input / "../outside.xlsx"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    recycle_entries(self.input, [path])
            recycle.assert_not_called()

    def test_recycle_deduplicates_nested_selection_and_stops_after_os_failure(self):
        batch = self.input / "统计_旧批次"
        batch.mkdir(parents=True)
        child = batch / "结果.xlsx"
        child.touch()
        with patch("attendance_analyzer.file_manager.send2trash") as recycle:
            self.assertEqual(recycle_entries(self.input, [batch, child, batch]), 1)
            recycle.assert_called_once_with(str(batch))
        first, second = self.input / "甲.xlsx", self.input / "乙.xlsx"
        first.touch()
        second.touch()
        with patch("attendance_analyzer.file_manager.send2trash", side_effect=[None, PermissionError("Excel opened")]) as recycle:
            with self.assertRaisesRegex(OSError, "已移走 1/2 项"):
                recycle_entries(self.input, [first, second])
            self.assertEqual(recycle.call_count, 2)

    @unittest.skipUnless(os.name == "nt", "Windows 回收站 COM 错误验证")
    def test_recycle_com_initialization_failure_reports_progress_and_preserves_files(self):
        import pywintypes

        list_entries(self.input)
        first, second = self.input / "甲.xlsx", self.input / "乙.xlsx"
        first.touch()
        second.touch()
        error = pywintypes.com_error(-2147221164, "Class not registered", None, None)
        with patch("attendance_analyzer.file_manager.send2trash", side_effect=error) as recycle:
            with self.assertRaisesRegex(OSError, "已移走 0/2 项") as caught:
                recycle_entries(self.input, [first, second])
            recycle.assert_called_once()
        self.assertIs(caught.exception.__cause__, error)
        self.assertIn("Class not registered", str(caught.exception))
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())

    @unittest.skipUnless(os.name == "nt", "Windows 目录联接验证")
    def test_junction_cannot_redirect_managed_data_to_external_directory(self):
        import subprocess

        list_entries(self.input)
        junction = self.input / "外部目录"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(self.source)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            with self.assertRaises(ValueError):
                managed_path(self.input, junction / "考勤.xlsx")
            with patch("attendance_analyzer.file_manager.send2trash") as recycle:
                with self.assertRaises(ValueError):
                    recycle_entries(self.input, [junction])
                recycle.assert_not_called()
        finally:
            junction.rmdir()


if __name__ == "__main__":
    unittest.main()
