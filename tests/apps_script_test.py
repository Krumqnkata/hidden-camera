r"""Тест на AppsScriptUploader срещу локален фалшив Apps Script (с 302 пренасочване като при Google).

Пускане:  .venv\Scripts\python tests\apps_script_test.py
"""

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from hcam.config import Config, parse_upload_targets  # noqa: E402
from hcam.uploaders import AppsScriptUploader, AuthRequiredError, UploadError  # noqa: E402
from hcam.winutils import find_ffmpeg  # noqa: E402

SECRET = "s3cr3t"
DRIVE: dict[str, dict[str, bytes]] = {}  # date -> {name: bytes}
RESULTS: dict[str, bytes] = {}
MODE = {"html": False}


class FakeAppsScript(BaseHTTPRequestHandler):
    def log_message(self, *a):  # тихо
        pass

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if MODE["html"]:
            out = b"<html>Error</html>"
        else:
            req = json.loads(body)
            if req.get("secret") != SECRET:
                res = {"ok": False, "error": "bad secret"}
            elif req.get("action") == "ping":
                res = {"ok": True, "folder": "https://drive.google.com/drive/folders/FAKE"}
            else:
                folder = DRIVE.setdefault(req["date"], {})
                existed = req["name"] in folder
                folder.setdefault(req["name"], base64.b64decode(req["data"]))
                res = {"ok": True, "id": "id-" + req["name"], "existed": existed}
            out = json.dumps(res).encode()
        key = uuid.uuid4().hex
        RESULTS[key] = out
        self.send_response(302)  # като Google: отговорът се взема с GET от друг URL
        self.send_header("Location", f"/echo?k={key}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        out = RESULTS.pop(self.path.split("k=", 1)[-1], b"")
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if out.startswith(b"<") else "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAppsScript)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}/macros/s/FAKE/exec"

base = Path(tempfile.mkdtemp(prefix="hcam_gs_"))
cfg = Config(base_dir=base, gdrive_script_url=url, gdrive_script_secret=SECRET)
cfg.ensure_dirs()

# --- UPLOAD_METHOD парсване
assert parse_upload_targets("apps script") == ["gscript"]
assert parse_upload_targets("Apps Script, Telegram") == ["gscript", "telegram"]
assert parse_upload_targets("google drive api") == ["gdrive"]
assert parse_upload_targets("none") == []
try:
    parse_upload_targets("dropbox")
    raise SystemExit("unknown method should fail")
except ValueError:
    pass
print("upload method parsing OK")

# --- истински малък клип (3 s тест картина + тон)
clip_dir = cfg.recordings_dir / "2026-11-16"
clip_dir.mkdir(parents=True)
clip = clip_dir / "2026-11-16_120000_cam0_Laptop.mp4"
subprocess.run([find_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=15",
                "-f", "lavfi", "-i", "sine=frequency=440", "-t", "3", "-c:v", "libx264", "-crf", "18",
                "-c:a", "aac", str(clip)], check=True)
orig = clip.read_bytes()
print(f"clip {len(orig) / 1024:.0f} KB")

up = AppsScriptUploader(cfg)
assert up.ping() == "https://drive.google.com/drive/folders/FAKE"
print("ping OK (302 redirect followed)")

ref = up.upload(clip, "2026-11-16")
assert DRIVE["2026-11-16"][clip.name] == orig, "uploaded bytes differ"
print("upload OK, bytes identical, id =", ref)

up.upload(clip, "2026-11-16")  # повторно — без дубликат
assert len(DRIVE["2026-11-16"]) == 1
print("re-upload deduplicated OK")

# --- твърде голям -> компресира до 480p
DRIVE.clear()
cfg.gdrive_script_max_mb = (len(orig) - 1) / 1024 / 1024
small_up = AppsScriptUploader(cfg)
small_up.upload(clip, "2026-11-16")
sent = DRIVE["2026-11-16"][clip.name]
assert len(sent) < len(orig), "expected a smaller re-encoded copy"
assert not list(cfg.tmp_dir.glob("gs_*")), "temp shrink file not cleaned"
print(f"oversize -> shrunk to {len(sent) / 1024:.0f} KB OK")

cfg.gdrive_script_max_mb = 0.001
assert AppsScriptUploader(cfg).upload(clip, "2026-11-16") == "too_large"
print("still too large -> 'too_large' (kept local) OK")
cfg.gdrive_script_max_mb = 30

# --- грешки
cfg.gdrive_script_secret = "wrong"
try:
    AppsScriptUploader(cfg).ping()
    raise SystemExit("bad secret should raise")
except AuthRequiredError as e:
    print("bad secret ->", e)
cfg.gdrive_script_secret = SECRET

MODE["html"] = True
try:
    AppsScriptUploader(cfg).ping()
    raise SystemExit("html should raise")
except UploadError as e:
    print("HTML response ->", type(e).__name__, str(e)[:60])
MODE["html"] = False

cfg.gdrive_script_url = "http://127.0.0.1:1/exec"  # няма сървър
try:
    AppsScriptUploader(cfg).ping()
    raise SystemExit("offline should raise")
except UploadError as e:
    assert not isinstance(e, AuthRequiredError)
    print("offline ->", e)

srv.shutdown()
shutil.rmtree(base, ignore_errors=True)
print("\nALL APPS SCRIPT TESTS PASSED")
