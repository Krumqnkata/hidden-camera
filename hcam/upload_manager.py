"""Опашка за качване, устойчива на рестарти и липса на интернет.

Източникът на истина е файловата система: всеки .mp4 в recordings/YYYY-MM-DD/,
за който в state/uploads.json няма отметка за дадена услуга, е „чакащ“.
Така клиповете, записани без интернет (или преди срив), се качват автоматично,
щом връзката се възстанови.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from pathlib import Path

from .config import Config
from .uploaders import AuthRequiredError, Uploader, UploadError
from .winutils import free_disk_gb

log = logging.getLogger("hcam.upload")

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAINTENANCE_INTERVAL = 3600.0
MAX_BACKOFF = 600.0


class UploadManager(threading.Thread):
    def __init__(self, cfg: Config, uploaders: list[Uploader], stop_event: threading.Event):
        super().__init__(name="Uploader", daemon=True)
        self.cfg = cfg
        self.uploaders = uploaders
        self._stop_event = stop_event
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self.manifest_path = cfg.state_dir / "uploads.json"
        self.manifest: dict[str, dict[str, str]] = self._load_manifest()
        self._backoff = 0.0
        self._last_maintenance = 0.0
        self._auth_warned: dict[str, float] = {}
        self.online: bool | None = None
        self.last_upload: str | None = None
        self.pending_count = 0

    # ---------------- manifest ----------------
    def _load_manifest(self) -> dict[str, dict[str, str]]:
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_manifest(self) -> None:
        with self._lock:
            tmp = self.manifest_path.with_suffix(".part")
            tmp.write_text(json.dumps(self.manifest, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.manifest_path)

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.cfg.recordings_dir).as_posix()

    def _done_targets(self, path: Path) -> set[str]:
        return set(self.manifest.get(self._rel(path), {}))

    def fully_uploaded(self, path: Path) -> bool:
        need = {u.key for u in self.uploaders}
        return need.issubset(self._done_targets(path))

    # ---------------- API ----------------
    def notify(self, path: Path | None = None) -> None:  # noqa: ARG002
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def _sleep(self, seconds: float) -> None:
        self._wake.clear()
        self._wake.wait(seconds)

    # ---------------- scan ----------------
    def _all_clips(self) -> list[Path]:
        root = self.cfg.recordings_dir
        if not root.is_dir():
            return []
        clips: list[Path] = []
        for d in sorted(root.iterdir()):
            if d.is_dir() and DATE_DIR_RE.match(d.name):
                clips.extend(sorted(d.glob("*.mp4")))
        return clips

    def _pending(self) -> list[tuple[Path, Uploader]]:
        out = []
        for clip in self._all_clips():
            done = self._done_targets(clip)
            for up in self.uploaders:
                if up.key not in done:
                    out.append((clip, up))
        return out

    # ---------------- loop ----------------
    def run(self) -> None:
        if not self.uploaders:
            log.info("Качването е изключено (UPLOAD_METHOD=none) — клиповете остават само локално")
        else:
            log.info("Качване към: %s", ", ".join(u.key for u in self.uploaders))
        while not self._stop_event.is_set():
            try:
                if self.uploaders:
                    self._process_pending()
                if time.monotonic() - self._last_maintenance > MAINTENANCE_INTERVAL:
                    self._last_maintenance = time.monotonic()
                    self._maintenance()
            except Exception:  # noqa: BLE001
                log.exception("Неочаквана грешка в UploadManager")
            wait = self._backoff if self._backoff else self.cfg.upload_scan_interval
            self._sleep(wait)
        log.info("UploadManager е спрян")

    def _process_pending(self) -> None:
        pending = self._pending()
        self.pending_count = len(pending)
        if not pending:
            self._backoff = 0.0
            return
        reachable: dict[str, bool] = {}
        for clip, up in pending:
            if self._stop_event.is_set():
                return
            # не качвай нищо, което е по-младо от 3 с (за всеки случай)
            if time.time() - clip.stat().st_mtime < 3:
                continue
            if up.key not in reachable:
                reachable[up.key] = up.is_reachable()
                if not reachable[up.key]:
                    if self.online is not False:
                        log.warning("Няма връзка с %s — %d клипа чакат локално", up.host, len(pending))
                    self.online = False
            if not reachable[up.key]:
                continue
            if self.online is False:
                log.info("Връзката е възстановена — качвам %d чакащи", len(pending))
            self.online = True
            try:
                ref = up.upload(clip, clip.parent.name)
            except AuthRequiredError as e:
                last = self._auth_warned.get(up.key, 0.0)
                if time.monotonic() - last > 3600:
                    log.error("[%s] %s", up.key, e)
                    self._auth_warned[up.key] = time.monotonic()
                reachable[up.key] = False  # пропусни тази услуга в този цикъл
                continue
            except UploadError as e:
                self._backoff = min(MAX_BACKOFF, max(30.0, (self._backoff or 15.0) * 2))
                if e.retry_after:
                    self._backoff = max(self._backoff, e.retry_after)
                log.warning("[%s] Неуспешно качване на %s: %s (нов опит след %.0f с)",
                            up.key, clip.name, e, self._backoff)
                return
            with self._lock:
                self.manifest.setdefault(self._rel(clip), {})[up.key] = ref
            self._save_manifest()
            self.last_upload = time.strftime("%Y-%m-%d %H:%M:%S")
            self.pending_count = max(0, self.pending_count - 1)
            self._backoff = 0.0
            if self.cfg.delete_local_after_upload and self.fully_uploaded(clip):
                clip.unlink(missing_ok=True)
                log.info("Изтрит локално след качване: %s", clip.name)

    # ---------------- поддръжка на диска ----------------
    def _can_delete(self, clip: Path) -> bool:
        return not self.uploaders or self.fully_uploaded(clip)

    def _maintenance(self) -> None:
        today = time.time()
        clips = self._all_clips()

        # 1) Ретенция
        if self.cfg.local_retention_days > 0:
            limit = today - self.cfg.local_retention_days * 86400
            for clip in clips:
                if clip.stat().st_mtime < limit and self._can_delete(clip):
                    clip.unlink(missing_ok=True)
                    log.info("Ретенция: изтрит стар клип %s", clip.name)

        # 2) Свободно място
        clips = self._all_clips()
        for clip in clips:  # най-старите първо
            if free_disk_gb(self.cfg.recordings_dir) >= self.cfg.min_free_disk_gb:
                break
            if self._can_delete(clip):
                clip.unlink(missing_ok=True)
                log.warning("Малко свободно място — изтрит качен клип %s", clip.name)
        free = free_disk_gb(self.cfg.recordings_dir)
        if free < self.cfg.min_free_disk_gb:
            log.error("Свободно място: %.1f GB (< %.1f GB) и няма качени клипове за изтриване!",
                      free, self.cfg.min_free_disk_gb)

        # 3) Празни папки по дати + почистване на манифеста
        for d in self.cfg.recordings_dir.iterdir():
            if d.is_dir() and DATE_DIR_RE.match(d.name) and not any(d.iterdir()):
                shutil.rmtree(d, ignore_errors=True)
        with self._lock:
            existing = {self._rel(c) for c in self._all_clips()}
            stale = [k for k in self.manifest if k not in existing]
            for k in stale:
                del self.manifest[k]
        if stale:
            self._save_manifest()
