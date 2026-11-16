"""Фоново обединяване на видео + аудио с ffmpeg -> recordings/YYYY-MM-DD/*.mp4"""

from __future__ import annotations

import logging
import queue
import shutil
import threading
from collections.abc import Callable
from pathlib import Path

from .config import Config
from .recorder import SessionMeta, safe_name
from .winutils import find_ffmpeg, run_hidden

log = logging.getLogger("hcam.merger")


class MergeWorker(threading.Thread):
    """Обработва сесии една по една (за да не товари CPU с паралелни ffmpeg-и)."""

    def __init__(self, cfg: Config, on_clip_ready: Callable[[Path], None]):
        super().__init__(name="Merger", daemon=True)
        self.cfg = cfg
        self.on_clip_ready = on_clip_ready
        self.q: queue.Queue[SessionMeta | None] = queue.Queue()
        self.ffmpeg = find_ffmpeg(cfg.ffmpeg_path, cfg.base_dir)
        if self.ffmpeg:
            log.info("ffmpeg: %s", self.ffmpeg)
        else:
            log.error("ffmpeg НЕ е намерен! Суровите файлове ще останат в %s", cfg.tmp_dir)

    def submit(self, meta: SessionMeta) -> None:
        self.q.put(meta)

    def shutdown(self, timeout: float = 120.0) -> None:
        self.q.put(None)
        self.join(timeout=timeout)

    # ------------------------------------------------------------------
    def recover_orphans(self) -> int:
        """При старт: довършва сесии, останали в _tmp след срив/рестарт."""
        n = 0
        for meta_file in sorted(Path(self.cfg.tmp_dir).glob("*.json")):
            try:
                meta = SessionMeta.load(meta_file)
            except Exception:  # noqa: BLE001
                log.exception("Невалиден meta файл %s", meta_file)
                continue
            log.warning("Възстановявам недовършена сесия %s (complete=%s)", meta.session_id, meta.complete)
            self.submit(meta)
            n += 1
        return n

    def run(self) -> None:
        while True:
            meta = self.q.get()
            if meta is None:
                break
            try:
                self._process(meta)
            except Exception:  # noqa: BLE001
                log.exception("Грешка при обединяване на сесия %s", meta.session_id)

    # ------------------------------------------------------------------
    def _encode(self, video: Path, audio: Path | None, out: Path, reencode: bool) -> bool:
        assert self.ffmpeg
        cmd = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video)]
        if audio:
            cmd += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
        if reencode:
            cmd += [
                "-c:v", "libx264",
                "-preset", self.cfg.video_preset,
                "-crf", str(self.cfg.video_crf),
                "-pix_fmt", "yuv420p",
                "-threads", str(self.cfg.ffmpeg_threads),
            ]
        else:
            cmd += ["-c:v", "copy"]
        cmd += ["-c:a", "aac", "-b:a", "96k"] if audio else ["-an"]
        cmd += ["-movflags", "+faststart", "-f", "mp4", str(out)]
        res = run_hidden(cmd, timeout=3600)
        if res.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            log.warning("ffmpeg (%s) върна %s: %s", "x264" if reencode else "copy",
                        res.returncode, res.stderr.decode("utf-8", "replace")[-800:])
            out.unlink(missing_ok=True)
            return False
        return True

    def _process(self, meta: SessionMeta) -> None:
        out_dir = self.cfg.recordings_dir / meta.date
        out_dir.mkdir(parents=True, exist_ok=True)
        audio = Path(meta.audio_file) if meta.audio_file else None
        if audio and (not audio.is_file() or audio.stat().st_size <= 44):
            log.warning("Аудио файлът липсва/празен за %s — само видео", meta.session_id)
            audio = None

        time_part = meta.session_id.split("_", 1)[-1]
        all_ok = True
        for track in meta.cams:
            video = Path(track.file)
            if not video.is_file() or video.stat().st_size == 0:
                log.warning("Видео файлът липсва: %s", video)
                continue
            final = out_dir / f"{meta.date}_{time_part}_cam{track.index}_{safe_name(track.name)}.mp4"
            part = final.with_suffix(".mp4.part")
            ok = False
            if self.ffmpeg:
                ok = self._encode(video, audio, part, reencode=True) or self._encode(video, audio, part, reencode=False)
            if ok:
                part.replace(final)
                video.unlink(missing_ok=True)
                size_mb = final.stat().st_size / 1024 / 1024
                log.info("✔ Клип готов: %s (%.1f MB)", final, size_mb)
                self.on_clip_ready(final)
            else:
                all_ok = False
                failed_dir = out_dir / "raw_failed"
                failed_dir.mkdir(exist_ok=True)
                shutil.move(str(video), failed_dir / video.name)
                if audio and audio.is_file():
                    shutil.copy2(audio, failed_dir / audio.name)
                log.error("Обединяването не успя — суровите файлове са запазени в %s", failed_dir)

        if audio:
            audio.unlink(missing_ok=True)
        meta.meta_path.unlink(missing_ok=True)
        if all_ok:
            log.debug("Сесия %s е обработена", meta.session_id)
