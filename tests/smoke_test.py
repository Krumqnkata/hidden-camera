r"""Smoke тест без хардуер: синтетични кадри + тон -> запис -> ffmpeg -> опашка за качване.

Пускане:  .venv\Scripts\python tests\smoke_test.py
"""
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from hcam.camera import CameraWorker  # noqa: E402
from hcam.config import CameraSpec, Config, parse_gdrive_folder_id  # noqa: E402
from hcam.logging_setup import setup_logging  # noqa: E402
from hcam.merger import MergeWorker  # noqa: E402
from hcam.recorder import RecordingSession  # noqa: E402
from hcam.single_instance import AlreadyRunningError, SingleInstance, send_command  # noqa: E402
from hcam.upload_manager import UploadManager  # noqa: E402
from hcam.uploaders.base import Uploader, UploadError  # noqa: E402
from hcam.winutils import find_ffmpeg  # noqa: E402

base = Path(tempfile.mkdtemp(prefix="hcam_smoke_"))
cfg = Config(base_dir=base, cameras=[CameraSpec(0, "Laptop")], upload_method="google drive",
             upload_scan_interval=0.5, post_trigger_seconds=1)
cfg.ensure_dirs()
setup_logging(cfg.log_file, "DEBUG")

# --- config helpers
assert cfg.upload_targets == ["gdrive"], cfg.upload_targets
assert Config(base_dir=base, upload_method="both").upload_targets == ["gdrive", "telegram"]
assert parse_gdrive_folder_id("https://drive.google.com/drive/folders/1AbC_d-9?usp=sharing") == "1AbC_d-9"
print("config OK")

# --- motion detection on synthetic frames
stop = threading.Event()
cam = CameraWorker(CameraSpec(0, "Laptop"), cfg, stop)
cam._opened_at = time.monotonic() - 100
cam.online = True
f1 = np.full((480, 640, 3), 80, np.uint8)
cam._detect_motion(f1, time.monotonic())
cam._detect_motion(f1.copy(), time.monotonic())
assert not cam.motion_active(time.monotonic()), "static scene should not trigger"
f2 = f1.copy()
f2[100:300, 200:400] = 220
cam._detect_motion(f2, time.monotonic())
assert cam.motion_active(time.monotonic()), f"moving block should trigger (score {cam.motion_score})"
print(f"motion OK (score {cam.motion_score:.1f}%)")


# --- fake camera feeding frames
class FakeAudio:
    online = True
    samplerate = 16000

    def __init__(self):
        self.q = None

    def start_recording(self):
        self.q = queue.Queue()
        return self.q

    def stop_recording(self):
        self.q = None


frame_counter = [0]


def feeder():
    while not stop.is_set():
        img = np.zeros((480, 640, 3), np.uint8)
        x = (frame_counter[0] * 10) % 600
        img[200:280, x:x + 40] = (0, 255, 0)
        with cam._lock:
            cam._frame, cam._frame_ts = img, time.monotonic()
        cam.frame_size = (640, 480)
        frame_counter[0] += 1
        time.sleep(1 / 30)


fa = FakeAudio()
threading.Thread(target=feeder, daemon=True).start()
time.sleep(0.2)
sess = RecordingSession(cfg, [cam], fa, lambda: "BOTH")
sess.start()
t = 0.0
while sess.elapsed < 2.0:  # 2 s of 440 Hz tone in 100 ms blocks
    block = (0.3 * np.sin(2 * np.pi * 440 * (np.arange(1600) / 16000 + t))).astype(np.float32)
    fa.q.put(block)
    t += 0.1
    time.sleep(0.1)
meta = sess.stop()
print("session:", meta.session_id, "frames:", [c.frames for c in meta.cams], "dur:", meta.duration)
assert meta.cams[0].frames >= 28, "expected ~30 frames at 15 FPS for 2 s"


# --- fake uploader: offline first, then online
class FakeUp(Uploader):
    key = "gdrive"
    host = "x"
    online = False
    uploaded: list = []

    def is_reachable(self, timeout=5.0):  # noqa: ARG002
        return self.online

    def upload(self, path, date):  # noqa: ARG002
        if not self.online:
            raise UploadError("offline")
        self.uploaded.append(path.name)
        return "fake-id"


up = FakeUp()
mgr = UploadManager(cfg, [up], stop)
mgr.start()
ready = []
merger = MergeWorker(cfg, on_clip_ready=lambda p: (ready.append(p), mgr.notify(p)))
merger.start()
merger.submit(meta)
merger.shutdown(timeout=120)
assert ready, "merge produced no clip"
clip = ready[0]
print("clip:", clip.relative_to(base), f"{clip.stat().st_size/1024:.0f} KB")
assert not list(cfg.tmp_dir.iterdir()), f"tmp not cleaned: {list(cfg.tmp_dir.iterdir())}"

probe = subprocess.run([find_ffmpeg(), "-hide_banner", "-i", str(clip)], capture_output=True, text=True)
streams = [ln.strip() for ln in probe.stderr.splitlines() if "Stream #" in ln or "Duration" in ln]
print("\n".join(streams))
assert any("h264" in s for s in streams) and any("aac" in s for s in streams)

time.sleep(4)  # clip mtime guard is 3 s
mgr.wake()
time.sleep(1)
assert not up.uploaded and mgr.pending_count == 1, "should be pending while offline"
print("offline: pending =", mgr.pending_count)
up.online = True
mgr._backoff = 0
mgr.wake()
for _ in range(50):
    if up.uploaded:
        break
    time.sleep(0.2)
assert up.uploaded == [clip.name], up.uploaded
print("after reconnect uploaded:", up.uploaded, "manifest:", mgr.manifest)

# --- single instance
si = SingleInstance(47999)
si.status_provider = lambda: {"ok": True}
si.acquire()
try:
    SingleInstance(47999).acquire()
    raise SystemExit("second instance should fail")
except AlreadyRunningError:
    print("single-instance OK")
print("STATUS:", send_command(47999, "STATUS"))
si.release()

stop.set()
mgr.join(timeout=5)
import logging  # noqa: E402

logging.shutdown()
shutil.rmtree(base, ignore_errors=True)
print("\nALL SMOKE TESTS PASSED")
