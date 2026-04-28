"""Threading HTTP + (later) SSE server, sharing the Store with the
asyncio source-task graph that fills it.

The store's ``RLock`` already makes concurrent reads safe; we never
write from the HTTP thread. A small ``ThreadingHTTPServer`` is plenty
for the loopback-only operator dashboard use case — one or two
browser tabs at a time.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import threading
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from . import handlers


_STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".ico":  "image/x-icon",
    ".woff2": "font/woff2",
}


def _make_handler_class(store, asset_root: str):
    """Build a request-handler class closed over (store, asset_root).

    BaseHTTPRequestHandler doesn't accept extra constructor args, so
    we generate a subclass per server instance.
    """

    class _Handler(http.server.BaseHTTPRequestHandler):
        # Quieter logs — the default per-request stderr line is too
        # noisy for an idle dashboard with SSE polling.
        def log_message(self, fmt, *args):  # noqa: N802
            pass

        def _write_json(self, status: int, payload) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type",
                             "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _write_static(self, rel_path: str) -> None:
            # Strip leading / and any .. components to keep us inside
            # asset_root no matter what the client sends.
            rel_path = rel_path.lstrip("/")
            safe = os.path.normpath(rel_path)
            if safe.startswith("..") or os.path.isabs(safe):
                self.send_error(403)
                return
            disk_path = os.path.join(asset_root, safe)
            if not os.path.isfile(disk_path):
                self.send_error(404)
                return
            ext = os.path.splitext(disk_path)[1].lower()
            ctype = _STATIC_MIME.get(ext, "application/octet-stream")
            try:
                with open(disk_path, "rb") as fh:
                    body = fh.read()
            except OSError:
                self.send_error(500)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            parsed = urlsplit(self.path)
            path = parsed.path
            qs_raw = parse_qs(parsed.query, keep_blank_values=True)
            # parse_qs returns lists; the handlers expect single values.
            qs = {k: v[0] if v else "" for k, v in qs_raw.items()}

            if path == "/" or path == "/index.html":
                self._write_static("index.html")
                return
            if path.startswith("/static/"):
                self._write_static(path[len("/static/"):])
                return
            if path.startswith("/api"):
                api_path = path[len("/api"):] or "/"
                handler = handlers.ROUTES.get(api_path)
                if handler is None:
                    self._write_json(404, {"error": "no such endpoint",
                                           "path": api_path})
                    return
                try:
                    status, payload = handler(store, qs)
                except Exception as e:
                    self._write_json(500, {"error": str(e),
                                           "type": type(e).__name__})
                    return
                self._write_json(status, payload)
                return
            self.send_error(404)

    return _Handler


class WebServer:
    """Thin wrapper around ``ThreadingHTTPServer`` for ``smwebmon``.

    Owns the listening socket, the request-handling threads, and the
    server thread that drives ``serve_forever``. Read-only with respect
    to the ``Store`` — writes happen elsewhere via the asyncio source
    tasks scheduled by ``smartmet_top.runtime.start_sources``.
    """

    def __init__(self, store, *, bind: Tuple[str, int],
                 asset_root: str) -> None:
        self.store = store
        self.bind = bind
        self.asset_root = asset_root
        self._httpd: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        if self._httpd is None:
            return 0
        return self._httpd.server_port

    def start(self) -> None:
        if self._httpd is not None:
            return
        handler_cls = _make_handler_class(self.store, self.asset_root)
        # Allow rapid restart during development without TIME_WAIT
        # blocking the bind.
        http.server.ThreadingHTTPServer.allow_reuse_address = True
        self._httpd = http.server.ThreadingHTTPServer(self.bind, handler_cls)
        # IPv4 vs IPv6: the default uses AF_INET; explicit binds in the
        # operator's config file should "just work" for both. Nothing
        # to do here — http.server handles IPv6 if `bind[0]` is an
        # IPv6 literal.
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="smwebmon-http",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None
