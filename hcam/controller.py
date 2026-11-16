"""Оркестратор: OR логика на тригерите, старт/стоп на записа, нишки, статус."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

import cv2

from .audio import AudioMonitor
from .camera import CameraWorker
from .config import Config
from .merger import MergeWorker
from .overlay import annotate
from .recorder import RecordingSession
from .upload_manager import UploadManager
from .uploaders import AppsScriptUploader, GDriveUploader, TelegramUploader, Uploader, UploadError
from .winutils import keep_system_awake

log = logging.getLogger("hcam.core")

LOOP_HZ = 10
HEARTBEAT_SECONDS = 600


class SecuritySystem:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.stop_event = threading.Event()
        self.started_at = datetime.now()

        self.cameras = [CameraWorker(spec, cfg, self.stop_event) for spec in cfg.cameras]
        self.audio = AudioMonitor(cfg, self.stop_event) if cfg.audio_enabled else None

        uploaders: list[Uploader] = []
        self.telegram: TelegramUploader | None = None
        if cfg.telegram_configured:
            self.telegram = TelegramUploader(cfg)
        for target in cfg.upload_targets:
            if target == "gscript":
                uploaders.append(AppsScriptUploader(cfg))
            elif target == "gdrive":
                uploaders.append(GDriveUploader(cfg))
            elif target == "telegram":
                if self.telegram:
                    uploaders.append(self.telegram)
                else:
                    log.error("UPLOAD_METHOD включва telegram, но TELEGRAM_BOT_TOKEN/CHAT_ID липсват в .env")
        self.uploads = UploadManager(cfg, uploaders, self.stop_event)
        self.merger = MergeWorker(cfg, on_clip_ready=self.uploads.notify)

        self.session: RecordingSession | None = None
        self.last_trigger_ts = 0.0
        self.trigger_label = "MOTION"
        self.events_today = 0

    # ---------------- тригери ----------------
    def _evaluate(self, now: float) -> str | None:
        motion = any(c.motion_active(now) for c in self.cameras)
        sound = self.audio.audio_active(now) if self.audio else False
        if motion and sound:
            return "BOTH"
        if motion:
            return "MOTION"
        if sound:
            return "AUDIO"
        return None

    def _label(self) -> str:
        return self.trigger_label

    # ---------------- запис ----------------
    def _start_session(self, reason: str) -> None:
        cams = [c for c in self.cameras if c.online]
        if not cams and not (self.audio and self.audio.online):
            log.warning("Тригер %s, но няма активни устройства за запис", reason)
            return
        self.session = RecordingSession(self.cfg, cams, self.audio, self._label)
        self.session.start()
        self.events_today += 1

    def _stop_session(self) -> None:
        if self.session is None:
            return
        meta = self.session.stop()
        self.session = None
        self.merger.submit(meta)

    # ---------------- известие със снимка ----------------
    def _send_snapshot(self, trigger: str) -> None:
        if not (self.telegram and self.cfg.telegram_snapshot_on_trigger):
            return
        frame, cam = None, None
        for c in self.cameras:
            f, _ = c.latest_frame()
            if f is not None:
                frame, cam = f.copy(), c
                break
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        caption = f"⚠ [{trigger}] {stamp}"

        def _worker():
            try:
                if frame is not None and cam is not None:
                    annotate(frame, cam.spec.name, trigger)
                    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ok:
                        self.telegram.send_photo(jpg.tobytes(), caption)
                        return
                self.telegram.send_message(caption)
            except UploadError as e:
                log.info("Snapshot към Telegram не е изпратен: %s", e)
            except Exception:  # noqa: BLE001
                log.exception("Snapshot грешка")

        threading.Thread(target=_worker, name="Snapshot", daemon=True).start()

    # ---------------- статус ----------------
    def status(self) -> dict:
        now = time.monotonic()
        return {
            "running_since": self.started_at.isoformat(timespec="seconds"),
            "recording": self.session is not None,
            "recording_seconds": round(self.session.elapsed, 1) if self.session else 0,
            "trigger": self._evaluate(now),
            "cameras": [
                {"name": c.spec.name, "index": c.spec.index, "online": c.online,
                 "blank_picture": c.blank, "motion_pct": round(c.motion_score, 2), "size": c.frame_size}
                for c in self.cameras
            ],
            "audio": {"online": bool(self.audio and self.audio.online),
                      "muted": bool(self.audio and self.audio.muted),
                      "rms": round(self.audio.rms, 4) if self.audio else None},
            "uploads": {"targets": [u.key for u in self.uploads.uploaders],
                        "pending": self.uploads.pending_count, "online": self.uploads.online,
                        "last_upload": self.uploads.last_upload},
            "events_since_start": self.events_today,
        }

    def request_stop(self) -> None:
        self.stop_event.set()

    # ---------------- главен цикъл ----------------
    def run(self) -> None:
        log.info("Стартиране: %d камера(и), аудио=%s, качване=%s",
                 len(self.cameras), "да" if self.audio else "не", self.cfg.upload_targets or "няма")
        if self.cfg.keep_system_awake:
            keep_system_awake(True)
        for c in self.cameras:
            c.start()
        if self.audio:
            self.audio.start()
        self.merger.start()
        self.uploads.start()
        n = self.merger.recover_orphans()
        if n:
            log.info("Възстановени %d недовършени сесии", n)

        last_beat = time.monotonic()
        last_awake = time.monotonic()
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                trig = self._evaluate(now)
                if trig:
                    self.last_trigger_ts = now
                    if trig != self.trigger_label and self.session:
                        log.debug("Тригер: %s", trig)
                    self.trigger_label = trig

                if self.session is None:
                    if trig:
                        self._start_session(trig)
                        if self.session:
                            self._send_snapshot(trig)
                else:
                    if now - self.last_trigger_ts > self.cfg.post_trigger_seconds:
                        self._stop_session()
                    elif self.session.elapsed >= self.cfg.max_clip_seconds:
                        log.info("Достигнат MAX_CLIP_SECONDS — разделям на нов клип")
                        self._stop_session()
                        self._start_session(self.trigger_label)

                if now - last_awake > 60 and self.cfg.keep_system_awake:
                    keep_system_awake(True)
                    last_awake = now
                if now - last_beat > HEARTBEAT_SECONDS:
                    last_beat = now
                    s = self.status()
                    log.info("♥ cams=%s audio=%s pending_uploads=%s",
                             [(c["name"], c["online"]) for c in s["cameras"]],
                             s["audio"]["online"], s["uploads"]["pending"])
                self.stop_event.wait(1.0 / LOOP_HZ)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        log.info("Спиране...")
        self.stop_event.set()
        try:
            self._stop_session()
        except Exception:  # noqa: BLE001
            log.exception("Грешка при спиране на записа")
        self.merger.shutdown(timeout=180)
        for c in self.cameras:
            c.join(timeout=5)
        if self.audio:
            self.audio.join(timeout=5)
        self.uploads.wake()
        self.uploads.join(timeout=5)
        if self.cfg.keep_system_awake:
            keep_system_awake(False)
        log.info("Приложението е спряно.")
