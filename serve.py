#!/usr/bin/env python3
"""Static server for Peka Tools + suggestion endpoint (stdlib only).

Serves the project directory on 127.0.0.1:8094. POST /api/usulan accepts
{"repo", "reason", "name"} (validated: GitHub URL shape, reason >= 20 chars),
rate-limited to 5/hour per IP, and appends to data/usulan.json.
Suggestions are write-only: they are never served back publicly.
"""
import json
import os
import re
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
REPO_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/?$")
RATE = {}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE, **kwargs)

    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/api/usulan":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 4096:
                return self._json(413, {"ok": False, "error": "payload too large"})
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"ok": False, "error": "bad json"})
        repo = str(payload.get("repo", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        name = str(payload.get("name", "")).strip()[:80]
        if not REPO_RE.match(repo):
            return self._json(400, {"ok": False, "error": "link harus https://github.com/owner/repo"})
        if len(reason) < 20 or len(reason) > 2000:
            return self._json(400, {"ok": False, "error": "alasan minimal 20 karakter"})
        ip = self.client_address[0]
        now = time.time()
        RATE[ip] = [t for t in RATE.get(ip, []) if now - t < 3600]
        if len(RATE[ip]) >= 5:
            return self._json(429, {"ok": False, "error": "terlalu banyak usulan, coba lagi nanti"})
        RATE[ip].append(now)
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, "usulan.json")
        items = []
        if os.path.exists(path):
            try:
                items = json.load(open(path))
            except Exception:
                items = []
        items.append({"ts": int(now), "repo": repo, "reason": reason, "name": name, "ip": ip})
        with open(path, "w") as fh:
            json.dump(items, fh, ensure_ascii=False, indent=1)
        return self._json(200, {"ok": True})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8094), Handler).serve_forever()
