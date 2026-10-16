"""无控制台启动本机网页工作台，页面与计算服务不对局域网开放。"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser

from .file_manager import ensure_directory
from .web_api import WebApi


ASSETS = Path(__file__).parent / 'web'
METHODS = {'files', 'begin_upload', 'import_files', 'preview_recycle', 'recycle',
           'open_path', 'get_settings', 'save_settings', 'add_holiday_range', 'start', 'poll', 'close'}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, base_dir: Path):
        super().__init__(('127.0.0.1', 0), Handler)
        self.api = WebApi(base_dir)
        self.token = secrets.token_urlsafe(32)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.url = f'{self.origin}/{self.token}/'
        self.last_seen = time.monotonic()
        self.stopping = threading.Event()

    def stop(self):
        self.stopping.set()
        self.shutdown()

    def monitor(self):
        # 关闭标签页后留出刷新/重开的时间；计算期间绝不自动终止。
        while not self.stopping.wait(5):
            with self.api._lock:
                idle = not self.api._running and not self.api._busy
                if idle and time.monotonic() - self.last_seen > 180:
                    self.api.close()
                    self.stop()
                    return


class Handler(BaseHTTPRequestHandler):
    server: LocalServer

    def log_message(self, *_):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def _send(self, status, content, content_type='application/json; charset=utf-8'):
        if not isinstance(content, bytes):
            content = json.dumps(content, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(content)

    def _route(self):
        if self.headers.get('Host') != self.server.origin.removeprefix('http://'):
            self._send(403, {'ok': False, 'error': '此服务仅供本机工作台访问。'})
            return None
        prefix = f'/{self.server.token}/'
        if not self.path.startswith(prefix):
            self._send(404, {'ok': False, 'error': '页面地址已失效，请双击启动程序。'})
            return None
        return self.path[len(prefix):]

    def do_GET(self):
        route = self._route()
        if route is None:
            return
        if route == 'ping':
            self._send(200, {'ok': True})
            return
        assets = {'': ('index.html', 'text/html'), 'app.css': ('app.css', 'text/css'),
                  'app.js': ('app.js', 'text/javascript')}
        if route not in assets:
            self._send(404, {'ok': False, 'error': '页面不存在。'})
            return
        filename, kind = assets[route]
        self.server.last_seen = time.monotonic()
        self._send(200, (ASSETS / filename).read_bytes(), kind + '; charset=utf-8')

    def do_POST(self):
        route = self._route()
        if route is None:
            return
        if (self.headers.get('X-Attendance-Client') != '1'
                or self.headers.get('Origin', self.server.origin) != self.server.origin):
            self._send(403, {'ok': False, 'error': '请从本机工作台操作。'})
            return
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if length < 0:
                raise ValueError('无法确定传输大小，请重新选择文件。')
            if route.startswith('upload/'):
                _, token, index = route.split('/')
                self.server.api._receive_upload(token, int(index), self.rfile, length)
                result = {'ok': True, 'data': None}
            elif route.startswith('api/') and route[4:] in METHODS:
                method = route[4:]
                if length > 4 * 1024 * 1024:
                    raise ValueError('请求内容过大，请分批处理。')
                body = json.loads(self.rfile.read(length).decode('utf-8'))
                if not isinstance(body, dict):
                    raise ValueError('请求格式无效。')
                result = getattr(self.server.api, method)(**body)
                if method == 'poll':
                    self.server.last_seen = time.monotonic()
                if method == 'close' and result['ok']:
                    self._send(200, result)
                    threading.Thread(target=self.server.stop, daemon=True).start()
                    return
            else:
                self._send(404, {'ok': False, 'error': '操作不存在。'})
                return
        except (OSError, ValueError, TypeError) as error:
            self._send(400, {'ok': False, 'error': str(error)})
            return
        self._send(200, result)


def _lock_file(path):
    stream = path.open('a+b')
    # Windows 字节锁会禁止其他句柄读取该字节，检查文件长度即可。
    if os.fstat(stream.fileno()).st_size == 0:
        stream.write(b'0')
        stream.flush()
    stream.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        return None
    return stream


def _open_browser(url):
    if not webbrowser.open(url):
        raise OSError(f'无法打开默认浏览器，请手动打开此本机地址：\n{url}')


def launch(base_dir):
    logs = base_dir / 'logs'
    ensure_directory(logs)
    lock = _lock_file(logs / '.client.lock')
    session = logs / '.client-session.json'
    if lock is None:
        for _ in range(20):
            try:
                url = json.loads(session.read_text(encoding='utf-8'))['url']
                # 地址只接受本进程使用的回环形式，避免损坏记录导航到外部网站。
                from urllib.parse import urlsplit
                parts = urlsplit(url)
                if parts.scheme != 'http' or parts.hostname != '127.0.0.1' or parts.username or parts.password:
                    raise ValueError('启动记录无效')
                with build_opener(ProxyHandler({})).open(url + 'ping', timeout=1) as response:
                    if json.load(response).get('ok'):
                        _open_browser(url)
                        return 0
            except (OSError, ValueError, KeyError, URLError):
                time.sleep(0.15)
        raise RuntimeError('另一个考勤程序正在启动或退出，请稍后再双击打开。')
    server = worker = None
    try:
        server = LocalServer(base_dir)
        session.write_text(json.dumps({'url': server.url}), encoding='utf-8')
        threading.Thread(target=server.monitor, daemon=True).start()
        worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.25), daemon=True)
        worker.start()
        _open_browser(server.url)
        worker.join()
    finally:
        if server:
            if worker:
                server.stop()
            else:
                server.stopping.set()
            server.server_close()
            server.api.close()
        session.unlink(missing_ok=True)
        lock.close()
    return 0


def main(argv=None):
    argparse.ArgumentParser(description='打开本机考勤网页工作台。').parse_args(argv)
    base = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    return launch(base)


if __name__ == '__main__':
    raise SystemExit(main())
