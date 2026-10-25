#!/usr/bin/env python3
"""Static server for Peka Tools + suggestion endpoint + admin API (stdlib only).

Serves the project directory on 127.0.0.1:8094.
- POST /api/usulan accepts {"repo", "reason", "name"} (validated: GitHub URL
  shape, reason >= 20 chars), rate-limited to 5/hour per IP, and appends to
  data/usulan.json. Suggestions are write-only: never served publicly.
- /api/admin/* (login, usulan list, approve, reject, repo-info) requires an
  admin session cookie. Credentials come from ADMIN_EMAIL and
  ADMIN_PASSWORD_SHA256 in the environment or a .env file next to this
  script (same admin account as the Peka store). Without them, admin login
  is disabled. Approving a suggestion fetches the repo from GitHub, checks
  it is not already catalogued, appends it to tools.json, and marks the
  suggestion approved.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
TOOLS_PATH = os.path.join(BASE, "tools.json")
USULAN_PATH = os.path.join(DATA_DIR, "usulan.json")
REPO_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/?$")
RATE = {}
LOGIN_FAILS = {}
SESSIONS = {}
LOCK = threading.Lock()


def _load_dotenv():
    path = os.path.join(BASE, ".env")
    if not os.path.exists(path):
        return
    try:
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


_load_dotenv()
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_HASH = os.environ.get("ADMIN_PASSWORD_SHA256", "")


def stars_display(stars):
    if stars < 1000:
        return str(stars)
    s = f"{stars / 1000:.1f}".replace(".", ",")
    return s[:-2] + "rb" if s.endswith(",0") else s + "rb"


def load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def save_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def fetch_github_repo(owner_repo):
    req = urllib.request.Request(
        "https://api.github.com/repos/" + owner_repo,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "peka-tools-admin"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE, **kwargs)

    def log_message(self, *args):
        pass

    def _json(self, code, obj, cookie=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self, limit=8192):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                return None, 413
            return json.loads(self.rfile.read(n) or b"{}"), 0
        except Exception:
            return None, 400

    def _session_email(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith("pt_admin="):
                token = part[len("pt_admin="):]
                sess = SESSIONS.get(token)
                if sess and sess[1] > time.time():
                    return sess[0]
                SESSIONS.pop(token, None)
        return None

    def _make_cookie(self, token, secure, expired=False):
        parts = [f"pt_admin={token}", "Path=/", "HttpOnly", "SameSite=Lax"]
        if secure:
            parts.append("Secure")
        if expired:
            parts.append("Expires=Thu, 01 Jan 1970 00:00:00 GMT")
        return "; ".join(parts)

    def _is_secure(self):
        return (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip() == "https"

    # ---- public suggestion endpoint (unchanged behavior) ----
    def _post_usulan(self):
        payload, err = self._read_json(limit=4096)
        if err:
            return self._json(err, {"ok": False, "error": "bad json" if err == 400 else "payload too large"})
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
        with LOCK:
            items = load_json(USULAN_PATH, [])
            if not isinstance(items, list):
                items = []
            items.append({"ts": int(now), "repo": repo, "reason": reason,
                          "name": name, "ip": ip, "status": "pending"})
            save_json_atomic(USULAN_PATH, items)
        return self._json(200, {"ok": True})

    # ---- admin endpoints ----
    def _admin_login(self):
        payload, err = self._read_json()
        if err:
            return self._json(err, {"ok": False, "error": "bad json"})
        email = str(payload.get("email", "")).strip()
        password = str(payload.get("password", ""))
        ip = self.client_address[0]
        now = time.time()
        LOGIN_FAILS[ip] = [t for t in LOGIN_FAILS.get(ip, []) if now - t < 900]
        if len(LOGIN_FAILS[ip]) >= 5:
            return self._json(429, {"ok": False, "error": "terlalu banyak percobaan, coba lagi nanti"})
        ok = bool(ADMIN_EMAIL and ADMIN_HASH)
        if ok:
            ok = hmac.compare_digest(email, ADMIN_EMAIL) and hmac.compare_digest(
                hashlib.sha256(password.encode()).hexdigest(), ADMIN_HASH)
        if not ok:
            LOGIN_FAILS[ip].append(now)
            return self._json(401, {"ok": False, "error": "email atau password salah"})
        LOGIN_FAILS[ip] = []
        token = secrets.token_hex(32)
        SESSIONS[token] = (email, now + 12 * 3600)
        return self._json(200, {"ok": True},
                          cookie=self._make_cookie(token, self._is_secure()))

    def _admin_logout(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            part = part.strip()
            if part.startswith("pt_admin="):
                SESSIONS.pop(part[len("pt_admin="):], None)
        return self._json(200, {"ok": True},
                          cookie=self._make_cookie("", self._is_secure(), expired=True))

    def _admin_list(self):
        email = self._session_email()
        if not email:
            return self._json(401, {"ok": False, "error": "login dulu"})
        items = load_json(USULAN_PATH, [])
        if not isinstance(items, list):
            items = []
        out = []
        for i, it in enumerate(items):
            out.append({"id": i, "ts": it.get("ts"), "repo": it.get("repo"),
                        "reason": it.get("reason"), "name": it.get("name"),
                        "status": it.get("status") or "pending",
                        "tool": it.get("tool")})
        return self._json(200, {"ok": True, "items": out})

    def _admin_repo_info(self):
        email = self._session_email()
        if not email:
            return self._json(401, {"ok": False, "error": "login dulu"})
        qs = parse_qs(urlparse(self.path).query)
        repo = (qs.get("repo") or [""])[0].strip()
        if not REPO_RE.match(repo):
            return self._json(400, {"ok": False, "error": "link harus https://github.com/owner/repo"})
        owner_repo = repo[len("https://github.com/"):].rstrip("/")
        try:
            d = fetch_github_repo(owner_repo)
        except Exception:
            return self._json(502, {"ok": False, "error": "gagal mengambil data GitHub, coba lagi"})
        return self._json(200, {
            "ok": True, "full_name": d["full_name"], "url": d["html_url"],
            "stars": d["stargazers_count"], "stars_display": stars_display(d["stargazers_count"]),
            "forks": d["forks_count"], "language": d.get("language"),
            "license": (d.get("license") or {}).get("spdx_id") or "-",
            "description": d.get("description") or "",
        })

    def _admin_approve(self):
        email = self._session_email()
        if not email:
            return self._json(401, {"ok": False, "error": "login dulu"})
        payload, err = self._read_json()
        if err:
            return self._json(err, {"ok": False, "error": "bad json"})
        try:
            idx = int(payload.get("id"))
        except Exception:
            return self._json(400, {"ok": False, "error": "id tidak valid"})
        category = str(payload.get("category", "")).strip()
        desc = str(payload.get("desc", "")).strip()
        cocok = str(payload.get("cocok", "")).strip()
        if not category:
            return self._json(400, {"ok": False, "error": "kategori wajib diisi"})
        with LOCK:
            items = load_json(USULAN_PATH, [])
            if not isinstance(items, list) or idx < 0 or idx >= len(items):
                return self._json(404, {"ok": False, "error": "usulan tidak ditemukan"})
            item = items[idx]
            if (item.get("status") or "pending") != "pending":
                return self._json(409, {"ok": False, "error": "usulan sudah diproses"})
            repo_url = str(item.get("repo", ""))
            if not REPO_RE.match(repo_url):
                return self._json(400, {"ok": False, "error": "link usulan tidak valid"})
            owner_repo = repo_url[len("https://github.com/"):].rstrip("/")
            try:
                d = fetch_github_repo(owner_repo)
            except Exception:
                return self._json(502, {"ok": False, "error": "gagal mengambil data GitHub, coba lagi"})
            full_name = d["full_name"]
            data = load_json(TOOLS_PATH, {})
            tools = data.get("tools", [])
            if any(t.get("name", "").lower() == full_name.lower() for t in tools):
                return self._json(409, {"ok": False, "error": "repo sudah ada di katalog"})
            owner, repo = full_name.split("/", 1)
            language = d.get("language")
            entry = {
                "name": full_name, "owner": owner, "repo": repo,
                "url": d["html_url"], "stars": d["stargazers_count"],
                "stars_display": stars_display(d["stargazers_count"]),
                "forks": d["forks_count"], "language": language,
                "license": (d.get("license") or {}).get("spdx_id") or "-",
                "desc": desc or (d.get("description") or full_name),
                "cocok": cocok or str(item.get("reason", ""))[:300],
                "category": category, "agent": category,
                "date": time.strftime("%Y-%m-%d", time.localtime()),
                "tags": [category] + ([language] if language else []),
                "origin": "USULAN",
            }
            tools.append(entry)
            data["tools"] = tools
            cats = data.get("categories", [])
            if category not in cats:
                cats.append(category)
                data["categories"] = cats
            save_json_atomic(TOOLS_PATH, data)
            item["status"] = "approved"
            item["tool"] = full_name
            item["reviewed_ts"] = int(time.time())
            save_json_atomic(USULAN_PATH, items)
        return self._json(200, {"ok": True, "tool": full_name})

    def _admin_reject(self):
        email = self._session_email()
        if not email:
            return self._json(401, {"ok": False, "error": "login dulu"})
        payload, err = self._read_json()
        if err:
            return self._json(err, {"ok": False, "error": "bad json"})
        try:
            idx = int(payload.get("id"))
        except Exception:
            return self._json(400, {"ok": False, "error": "id tidak valid"})
        with LOCK:
            items = load_json(USULAN_PATH, [])
            if not isinstance(items, list) or idx < 0 or idx >= len(items):
                return self._json(404, {"ok": False, "error": "usulan tidak ditemukan"})
            if (items[idx].get("status") or "pending") != "pending":
                return self._json(409, {"ok": False, "error": "usulan sudah diproses"})
            items[idx]["status"] = "rejected"
            items[idx]["reviewed_ts"] = int(time.time())
            save_json_atomic(USULAN_PATH, items)
        return self._json(200, {"ok": True})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/admin/me":
            email = self._session_email()
            if not email:
                return self._json(401, {"ok": False, "error": "login dulu"})
            return self._json(200, {"ok": True, "email": email})
        if path == "/api/admin/usulan":
            return self._admin_list()
        if path == "/api/admin/repo-info":
            return self._admin_repo_info()
        if path.startswith("/api/"):
            return self._json(404, {"ok": False, "error": "not found"})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/usulan":
            return self._post_usulan()
        if path == "/api/admin/login":
            return self._admin_login()
        if path == "/api/admin/logout":
            return self._admin_logout()
        if path == "/api/admin/approve":
            return self._admin_approve()
        if path == "/api/admin/reject":
            return self._admin_reject()
        return self._json(404, {"ok": False, "error": "not found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8094), Handler).serve_forever()
