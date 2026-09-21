#!/usr/bin/env python3
"""Minimal mock backend for verifying WritingTasksPage chapter runs states.

Mode is switched via POST /mock/mode {mode: ok|empty|error}.
Serves the built frontend from ../dist and mocks /api endpoints.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = {"current": "ok"}
DIST = os.path.join(os.path.dirname(__file__), "..", "dist")

TASK_ITEM = {
    "task_id": "chapter:3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    "task_type": "chapter",
    "entity_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    "book_id": "b-1",
    "book_title": "验证用书",
    "chapter_id": "ch-verify-1",
    "chapter_no": 7,
    "status": "failed",
    "progress": None,
    "current_step": "draft",
    "control_requested": None,
    "error": {"code": "PATCH_STALE", "detail": {"message": "模拟失败详情"}},
    "actions": ["retry"],
    "created_at": None, "updated_at": None,
    "started_at": "2026-09-21T01:00:00", "finished_at": None,
    "topic": None,
}

RUNS = [
    {"run_id": "r-1", "status": "failed", "current_step": "draft", "control_requested": None,
     "error_code": "PATCH_STALE", "error_detail": {"message": "stale patch"},
     "started_at": "2026-09-21T01:00:00", "finished_at": "2026-09-21T01:05:00"},
    {"run_id": "r-2", "status": "succeeded", "current_step": None, "control_requested": None,
     "error_code": None, "error_detail": None,
     "started_at": "2026-09-20T09:00:00", "finished_at": "2026-09-20T09:30:00"},
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self._json({"detail": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/mock/mode":
            n = int(self.headers.get("Content-Length", 0))
            MODE["current"] = json.loads(self.rfile.read(n) or b"{}").get("mode", "ok")
            return self._json({"mode": MODE["current"]})
        return self._json({"detail": "not found"}, 404)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/mock/mode":
            return self._json(MODE)
        if path == "/api/books":
            return self._json([{"book_id": "b-1", "title": "验证用书", "status": "writing"}])
        if path == "/api/tasks":
            return self._json({"items": [TASK_ITEM], "page": 1, "page_size": 50,
                               "total": 1, "pages": 1, "task_types": ["chapter"]})
        if path.endswith("/runs") and "/api/chapters/" in path:
            if MODE["current"] == "empty":
                return self._json([])
            if MODE["current"] == "error":
                return self._json({"detail": "internal failure"}, 500)
            return self._json(RUNS)
        if path == "/" or path == "":
            return self._file(os.path.join(DIST, "index.html"), "text/html")
        if path.startswith("/api/"):
            # Unknown API endpoints return JSON 404, never HTML
            return self._json({"detail": "not found"}, 404)
        # static assets
        safe = path.lstrip("/")
        fp = os.path.normpath(os.path.join(DIST, safe))
        if fp.startswith(os.path.abspath(DIST)) and os.path.isfile(fp):
            ctype = "application/javascript" if fp.endswith(".js") else (
                "text/css" if fp.endswith(".css") else "application/octet-stream")
            return self._file(fp, ctype)
        return self._file(os.path.join(DIST, "index.html"), "text/html")


if __name__ == "__main__":
    print("mock backend on :8899, dist at", os.path.abspath(DIST))
    ThreadingHTTPServer(("127.0.0.1", 8899), Handler).serve_forever()
