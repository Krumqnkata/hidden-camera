"""Синхронизиран запис: по един .mp4 на камера + общ .wav за една сесия (събитие)."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import cv2

from .audio import AudioMonitor, WavWriterThread
from .camera import CameraWorker
from .config import Config
from .overlay import annotate, no_signal_frame

log = logging.getLogger("hcam.recorder")

DEFAULT_SIZE = (640, 480)


def safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


@dataclass
class CamTrack:
    index: int
    name: str
    file: str
    frames: int = 0


@dataclass
class SessionMeta:
    session_id: str
    date: str
    started: str
    tmp_dir: str
    fps: float
    cams: list[CamTrack] = field(default_factory=list)
    audio_file: str | None = None
    audio_samplerate: int | None = None
    first_trigger: str = ""
    ended: str | None = None
    duration: float = 0.0
    complete: bool = False

    @property
    def meta_path(self) -> Path:
        return Path(self.tmp_dir) / f"{self.session_id}.json"

    def save(self) -> None:
        tmp = self.meta_path.with_suffix(".json.part")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.meta_path)

    @classmethod
    def load(cls, path: Path) -> SessionMeta:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["cams"] = [CamTrack(**c) for c in data.get("cams", [])]
        return cls(**data)


class VideoWriterThread(threading.Thread):
    """Пише кадри с постоянна честота ``fps`` спрямо стенния часовник.

    Ако камерата дава по-малко кадри (или е изключена), последният кадър се
    дублира / пише се „NO SIGNAL“. Така продължителността на видеото винаги е
    равна на реалното време -> аудиото и видеото остават синхронизирани.
    """

    def __init__(self, cam: CameraWorker, track: CamTrack, fps: float, trigger_fn: Callable[[], str]):
        super().__init__(name=f"VidWriter{cam.spec.index}", daemon=True)
        self.cam = cam
        self.track = track
        self.fps = fps
        self.trigger_fn = trigger_fn
        self._done = threading.Event()

    def finish(self) -> None:
        self._done.set()

    def run(self) -> None:
        writer: cv2.VideoWriter | None = None
        size = self.cam.frame_size or DEFAULT_SIZE
        start = time.monotonic()
        written = 0
        max_catchup = int(self.fps * 10)
        try:
            while not self._done.is_set():
                expected = int((time.monotonic() - start) * self.fps) + 1
                missing = expected - written
                if missing > 0:
                    frame, _ = self.cam.latest_frame()
                    if frame is None:
                        img = no_signal_frame(size, self.cam.spec.name)
                    else:
                        img = frame.copy()
                    if writer is None:
                        size = (img.shape[1], img.shape[0])
                        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                        writer = cv2.VideoWriter(self.track.file, fourcc, self.fps, size)
                        if not writer.isOpened():
                            log.error("Не мога да създам VideoWriter за %s", self.track.file)
                            return
                    if (img.shape[1], img.shape[0]) != size:
                        img = cv2.resize(img, size)
                    annotate(img, self.cam.spec.name, self.trigger_fn())
                    if missing > max_catchup:
                        log.warning("VideoWriter изостава с %d кадъра — пропускам", missing - max_catchup)
                        written += missing - max_catchup
                        missing = max_catchup
                    for _ in range(missing):
                        writer.write(img)
                    written += missing
                next_due = start + written / self.fps
                self._done.wait(max(0.0, next_due - time.monotonic()))
        except Exception:  # noqa: BLE001
            log.exception("Грешка във VideoWriter за '%s'", self.cam.spec.name)
        finally:
            if writer is not None:
                writer.release()
            self.track.frames = written


class RecordingSession:
    def __init__(
        self,
        cfg: Config,
        cams: list[CameraWorker],
        audio: AudioMonitor | None,
        trigger_fn: Callable[[], str],
    ):
        self.cfg = cfg
        self.cams = cams
        self.audio = audio
        self.trigger_fn = trigger_fn
        now = datetime.now()
        self.started_wall = now
        self.started_mono = time.monotonic()
        sid = now.strftime("%Y%m%d_%H%M%S")
        self.meta = SessionMeta(
            session_id=sid,
            date=now.strftime("%Y-%m-%d"),
            started=now.isoformat(timespec="seconds"),
            tmp_dir=str(cfg.tmp_dir),
            fps=cfg.record_fps,
            first_trigger=trigger_fn(),
        )
        self._vwriters: list[VideoWriterThread] = []
        self._wav: WavWriterThread | None = None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_mono

    def start(self) -> None:
        tmp = Path(self.cfg.tmp_dir)
        sid = self.meta.session_id
        for cam in self.cams:
            cam.recording = True
            track = CamTrack(
                index=cam.spec.index,
                name=cam.spec.name,
                file=str(tmp / f"{sid}_cam{cam.spec.index}_{safe_name(cam.spec.name)}.raw.mp4"),
            )
            self.meta.cams.append(track)
            vw = VideoWriterThread(cam, track, self.cfg.record_fps, self.trigger_fn)
            self._vwriters.append(vw)

        if self.audio is not None:
            q = self.audio.start_recording()
            if q is not None:
                wav_path = tmp / f"{sid}.raw.wav"
                self.meta.audio_file = str(wav_path)
                self.meta.audio_samplerate = self.audio.samplerate
                self._wav = WavWriterThread(q, wav_path, self.audio.samplerate)

        self.meta.save()
        if self._wav:
            self._wav.start()
        for vw in self._vwriters:
            vw.start()
        log.info(
            "▶ ЗАПИС %s стартира (тригер: %s, камери: %s, аудио: %s)",
            sid,
            self.meta.first_trigger,
            ", ".join(c.spec.name for c in self.cams) or "няма",
            "да" if self._wav else "не",
        )

    def stop(self) -> SessionMeta:
        if self.audio is not None:
            self.audio.stop_recording()
        for vw in self._vwriters:
            vw.finish()
        if self._wav:
            self._wav.finish()
        for vw in self._vwriters:
            vw.join(timeout=10)
        if self._wav:
            self._wav.join(timeout=10)
        for cam in self.cams:
            cam.recording = False
        self.meta.ended = datetime.now().isoformat(timespec="seconds")
        self.meta.duration = round(self.elapsed, 2)
        self.meta.complete = True
        self.meta.save()
        log.info("■ ЗАПИС %s спря (%.1f с)", self.meta.session_id, self.meta.duration)
        return self.meta
