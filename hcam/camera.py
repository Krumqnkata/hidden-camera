"""Нишка за една камера: четене на кадри, детекция на движение, автоматично преподключване."""

from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np

from .config import CameraSpec, Config

log = logging.getLogger("hcam.camera")

_BACKENDS = {
    "DSHOW": cv2.CAP_DSHOW,
    "MSMF": cv2.CAP_MSMF,
    "ANY": cv2.CAP_ANY,
}

MOTION_PROC_WIDTH = 320  # детекцията работи върху умалено копие -> минимален CPU
REOPEN_DELAY = 5.0
MAX_FAILED_READS = 25


class CameraWorker(threading.Thread):
    """Чете кадри в отделна нишка.

    * В режим на изчакване: ``idle_fps`` (5 FPS) — щади процесора и вентилатора.
    * По време на запис: ``record_fps`` (15 FPS).
    * Детекцията на движение (``cv2.absdiff``) винаги се прави ~5 пъти в секунда,
      за да е еднакво поведението на прага независимо от режима.
    * Ако камерата изчезне (USB е изваден), нишката не пада, а опитва да я
      отвори отново на всеки ``REOPEN_DELAY`` секунди.
    """

    def __init__(self, spec: CameraSpec, cfg: Config, stop_event: threading.Event):
        super().__init__(name=f"Cam{spec.index}", daemon=True)
        self.spec = spec
        self.cfg = cfg
        self._stop_event = stop_event
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._frame_ts = 0.0
        self._prev_gray: np.ndarray | None = None
        self._last_detect = 0.0
        self._opened_at = 0.0
        self.recording = False  # контролерът превключва FPS режима
        self.online = False
        self.last_motion_ts = 0.0
        self.motion_score = 0.0  # % променени пиксели при последната проверка
        self.frame_size: tuple[int, int] | None = None  # (w, h)

    # ---------------- публично API ----------------
    @property
    def name_label(self) -> str:
        return self.spec.name

    def latest_frame(self) -> tuple[np.ndarray | None, float]:
        with self._lock:
            return self._frame, self._frame_ts

    def motion_active(self, now: float) -> bool:
        return self.online and (now - self.last_motion_ts) <= self.cfg.trigger_hold_seconds

    # ---------------- вътрешни ----------------
    def _open(self) -> cv2.VideoCapture | None:
        backend = _BACKENDS.get(self.cfg.camera_backend.upper(), cv2.CAP_DSHOW)
        cap = cv2.VideoCapture(self.spec.index, backend)
        if not cap.isOpened():
            cap.release()
            return None
        # Редът е важен: първо FOURCC, после резолюция (иначе някои USB камери игнорират MJPG)
        if self.spec.force_mjpg:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if self.spec.resolution:
            w, h = self.spec.resolution
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_FPS, max(self.cfg.record_fps, 15))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # без натрупване на стари кадри
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            return None
        h, w = frame.shape[:2]
        self.frame_size = (w, h)
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)) if fourcc > 0 else "?"
        log.info("Камера '%s' (index %d) е отворена: %dx%d, FOURCC=%s",
                 self.spec.name, self.spec.index, w, h, fourcc_str.strip() or "?")
        return cap

    def _detect_motion(self, frame: np.ndarray, now: float) -> None:
        h, w = frame.shape[:2]
        scale = MOTION_PROC_WIDTH / float(w)
        small = cv2.resize(frame, (MOTION_PROC_WIDTH, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        prev = self._prev_gray
        self._prev_gray = gray
        if prev is None or prev.shape != gray.shape:
            return
        if now - self._opened_at < self.cfg.camera_warmup_seconds:
            return  # авто-експонацията още се настройва
        diff = cv2.absdiff(prev, gray)
        _, mask = cv2.threshold(diff, self.cfg.motion_threshold, 255, cv2.THRESH_BINARY)
        changed = cv2.countNonZero(mask) * 100.0 / mask.size
        self.motion_score = changed
        if changed >= self.cfg.motion_min_area_percent:
            if now - self.last_motion_ts > self.cfg.trigger_hold_seconds:
                log.debug("Движение на '%s': %.2f%% от кадъра", self.spec.name, changed)
            self.last_motion_ts = now

    def run(self) -> None:
        cap: cv2.VideoCapture | None = None
        failed = 0
        detect_interval = 1.0 / max(self.cfg.idle_fps, 0.5)
        while not self._stop_event.is_set():
            if cap is None:
                cap = self._open()
                if cap is None:
                    if self.online or failed == 0:
                        log.warning("Камера '%s' (index %d) не е достъпна — нов опит след %.0f с",
                                    self.spec.name, self.spec.index, REOPEN_DELAY)
                    self.online = False
                    failed = 1
                    self._stop_event.wait(REOPEN_DELAY)
                    continue
                self.online = True
                self._opened_at = time.monotonic()
                self._prev_gray = None
                failed = 0

            fps = self.cfg.record_fps if self.recording else self.cfg.idle_fps
            period = 1.0 / max(fps, 0.5)
            t0 = time.monotonic()

            try:
                ok, frame = cap.read()
            except cv2.error:
                ok, frame = False, None

            if not ok or frame is None:
                failed += 1
                if failed >= MAX_FAILED_READS:
                    log.error("Камера '%s' спря да връща кадри (изключена?). Освобождавам я.", self.spec.name)
                    try:
                        cap.release()
                    except Exception:  # noqa: BLE001
                        pass
                    cap = None
                    self.online = False
                    with self._lock:
                        self._frame = None
                    self._stop_event.wait(REOPEN_DELAY)
                else:
                    self._stop_event.wait(0.1)
                continue

            failed = 0
            now = time.monotonic()
            with self._lock:
                self._frame = frame
                self._frame_ts = now
            if now - self._last_detect >= detect_interval * 0.95:
                self._last_detect = now
                try:
                    self._detect_motion(frame, now)
                except cv2.error:
                    log.exception("Грешка при детекция на движение")

            sleep = period - (time.monotonic() - t0)
            if sleep > 0:
                self._stop_event.wait(sleep)

        if cap is not None:
            cap.release()
        self.online = False
        log.info("Камера '%s' е спряна", self.spec.name)
