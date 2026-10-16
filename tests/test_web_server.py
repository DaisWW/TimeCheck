import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request
from unittest.mock import patch

import main as entrypoint
from attendance_analyzer.config import AppConfig
from attendance_analyzer.gui import LocalServer, _lock_file, launch


class WebServerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        AppConfig().save(self.base / "config.json")
        self.server = LocalServer(self.base)
        self.worker = Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.stop()
        self.worker.join(3)
        self.server.server_close()
        self.server.api.close()
        self.temporary.cleanup()

    def request(self, path="", data=None, headers=None):
        request = Request(self.server.url + path, data=data, headers=headers or {})
        try:
            with self.opener.open(request, timeout=3) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def api(self, method, data=None):
        status, _, body = self.request("api/" + method, json.dumps(data or {}).encode("utf-8"), {
            "X-Attendance-Client": "1", "Content-Type": "application/json", "Origin": self.server.origin})
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_serves_bundled_page_and_assets_only_with_session_and_host(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        for path, kind in [("", "text/html"), ("app.css", "text/css"), ("app.js", "text/javascript")]:
            status, headers, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(kind, headers["Content-Type"])
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertTrue(body)
        self.assertEqual(self.request("../config.json")[0], 404)
        self.assertEqual(self.request("config.json")[0], 404)
        self.assertEqual(self.request("", headers={"Host": "external.invalid"})[0], 403)
        with self.assertRaises(HTTPError) as raised:
            self.opener.open(self.server.origin + "/", timeout=3)
        self.assertEqual(raised.exception.code, 404)

    def test_api_rejects_foreign_origin_missing_header_and_unknown_method(self):
        self.assertEqual(self.request("api/files", b"{}")[0], 403)
        self.assertEqual(self.request("api/files", b"{}", {"X-Attendance-Client": "1", "Origin": "https://external.invalid"})[0], 403)
        self.assertEqual(self.request("api/_directory", b"{}", {"X-Attendance-Client": "1"})[0], 404)
        self.assertEqual(self.request("api/files", b"[1]", {"X-Attendance-Client": "1"})[0], 400)

    def test_http_upload_validates_size_and_copies_unicode_filename(self):
        name, content = "研发中心 含空格的考勤.xlsx", b"file bytes"
        plan = self.api("begin_upload", {"files": [{"name": name, "size": len(content)}]})["data"]
        self.assertEqual(self.request(f"upload/{plan['token']}/0", b"bad", {"X-Attendance-Client": "1"})[0], 400)
        self.assertFalse((self.base / "input" / name).exists())
        status, _, body = self.request(f"upload/{plan['token']}/0", content, {"X-Attendance-Client": "1"})
        self.assertEqual(status, 200, body)
        self.assertTrue(self.api("import_files", {"token": plan["token"], "choice": "skip"})["ok"])
        self.assertEqual((self.base / "input" / name).read_bytes(), content)

    def test_heartbeat_updates_lifecycle_and_exit_waits_for_job(self):
        self.server.last_seen = 0
        self.api("poll")
        self.assertGreater(self.server.last_seen, 0)
        self.server.api._running = True
        self.assertFalse(self.api("close")["ok"])
        self.assertFalse(self.server.stopping.is_set())
        self.server.api._running = False
        self.assertTrue(self.api("close")["ok"])
        self.worker.join(3)
        self.assertFalse(self.worker.is_alive())

    def test_browser_launch_failure_is_reported_and_releases_lock(self):
        # 独立目录，防止干扰当前测试用服务。
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch("attendance_analyzer.gui._open_browser", side_effect=OSError("浏览器不可用")):
                with self.assertRaisesRegex(OSError, "浏览器不可用"):
                    launch(base)
            lock = _lock_file(base / "logs/.client.lock")
            self.assertIsNotNone(lock)
            lock.close()
            self.assertFalse((base / "logs/.client-session.json").exists())

    def test_repeated_launch_reuses_existing_service(self):
        logs = self.base / "logs"
        logs.mkdir()
        lock = _lock_file(logs / ".client.lock")
        self.assertIsNotNone(lock)
        try:
            (logs / ".client-session.json").write_text(json.dumps({"url": self.server.url}), encoding="utf-8")
            with patch("attendance_analyzer.gui._open_browser") as open_browser:
                self.assertEqual(launch(self.base), 0)
            open_browser.assert_called_once_with(self.server.url)
        finally:
            lock.close()

    def test_initialization_failure_releases_single_instance_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch("attendance_analyzer.gui.LocalServer", side_effect=OSError("服务不可用")):
                with self.assertRaisesRegex(OSError, "服务不可用"):
                    launch(base)
            lock = _lock_file(base / "logs/.client.lock")
            self.assertIsNotNone(lock)
            lock.close()

    def test_windowless_entrypoint_reports_missing_dependency_and_saves_log(self):
        real_import = __import__

        def missing_dependency(name, *args, **kwargs):
            if name == "attendance_analyzer.gui":
                raise ModuleNotFoundError("缺少 openpyxl")
            return real_import(name, *args, **kwargs)

        with patch.object(entrypoint, "__file__", str(self.base / "main.py")), \
                patch("builtins.__import__", side_effect=missing_dependency), \
                patch("ctypes.windll.user32.MessageBoxW") as message:
            self.assertEqual(entrypoint.main([]), 1)
        self.assertIn("缺少 openpyxl", message.call_args.args[1])
        log, = (self.base / "logs").glob("启动失败_*.log")
        self.assertIn("ModuleNotFoundError", log.read_text(encoding="utf-8-sig"))
