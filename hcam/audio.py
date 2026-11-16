"""Микрофон: RMS мониторинг в реално време + запис в .wav по време на събитие."""

from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np

from .config import Config

log = logging.getLogger("hcam.audio")

REOPEN_DELAY = 5.0
SILENT_RMS = 1e-5  # под това = цифрова нула
SILENT_SECONDS = 30.0


class AudioMonitor(threading.Thread):
    """Държи отворен InputStream. Callback-ът само смята RMS и (при запис) слага блока в опашка.

    Ако микрофонът изчезне, потокът се отваря наново на всеки ``REOPEN_DELAY`` секунди.
    """

    def __init__(self, cfg: Config, stop_event: threading.Event):
        super().__init__(name="Audio", daemon=True)
        self.cfg = cfg
        self._stop_event = stop_event
        self.samplerate: int = 0
        self.online = False
        self.rms = 0.0
        self.last_trigger_ts = 0.0
        self._rec_queue: queue.Queue[np.ndarray] | None = None
        self._rec_lock = threading.Lock()
        self._stream_error = threading.Event()
        self.muted = False  # микрофонът дава абсолютна тишина (mute бутон?)
        self._silent_since: float | None = None

    # ---------------- запис ----------------
    def start_recording(self) -> queue.Queue[np.ndarray] | None:
        if not self.online:
            return None
        with self._rec_lock:
            self._rec_queue = queue.Queue(maxsize=3000)
            return self._rec_queue

    def stop_recording(self) -> None:
        with self._rec_lock:
            self._rec_queue = None

    def audio_active(self, now: float) -> bool:
        return self.online and (now - self.last_trigger_ts) <= self.cfg.trigger_hold_seconds

    # ---------------- поток ----------------
    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        if status:
            log.debug("Аудио статус: %s", status)
        mono = indata[:, 0] if indata.ndim > 1 else indata
        rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64))))
        self.rms = rms
        if rms >= self.cfg.audio_threshold:
            self.last_trigger_ts = time.monotonic()
        q = self._rec_queue
        if q is not None:
            try:
                q.put_nowait(mono.copy())
            except queue.Full:
                pass

    def _check_silence(self, now: float) -> None:
        """Истински микрофон никога не дава абсолютна нула. Нула = изключен с бутон (mute) или поверителност."""
        if self.rms < SILENT_RMS:
            if self._silent_since is None:
                self._silent_since = now
            elif not self.muted and now - self._silent_since >= SILENT_SECONDS:
                self.muted = True
                log.warning("Микрофонът дава пълна тишина от %.0f с — изключен с бутон (mute) или от "
                            "Settings > Privacy > Microphone? Звук няма да се засича/записва.",
                            now - self._silent_since)
        else:
            if self.muted:
                log.info("Микрофонът отново дава звук")
            self._silent_since = None
            self.muted = False

    def _resolve_samplerate(self, sd) -> int:
        if self.cfg.audio_samplerate:
            return int(self.cfg.audio_samplerate)
        try:
            info = sd.query_devices(self.cfg.audio_device_index, "input")
            return int(info["default_samplerate"]) or 44100
        except Exception:  # noqa: BLE001
            return 44100

    def run(self) -> None:
        try:
            import sounddevice as sd
        except Exception:  # noqa: BLE001
            log.exception("sounddevice не може да се зареди — работа само с видео")
            return

        announced_offline = False
        while not self._stop_event.is_set():
            self._stream_error.clear()
            try:
                sr = self._resolve_samplerate(sd)
                stream = sd.InputStream(
                    device=self.cfg.audio_device_index,
                    channels=1,
                    samplerate=sr,
                    dtype="float32",
                    blocksize=max(256, sr // 10),  # ~100 ms блок -> стабилен RMS
                    callback=self._callback,
                    finished_callback=self._stream_error.set,
                )
                with stream:
                    self.samplerate = sr
                    self.online = True
                    announced_offline = False
                    dev = sd.query_devices(stream.device)
                    log.info("Микрофон: '%s' @ %d Hz (праг RMS=%.3f)", dev["name"], sr, self.cfg.audio_threshold)
                    while not self._stop_event.is_set() and stream.active and not self._stream_error.is_set():
                        self._stop_event.wait(0.5)
                        self._check_silence(time.monotonic())
                if not self._stop_event.is_set():
                    log.warning("Аудио потокът спря неочаквано — рестартирам")
            except Exception as e:  # noqa: BLE001
                if not announced_offline:
                    log.warning("Микрофонът не е достъпен (%s) — нов опит след %.0f с", e, REOPEN_DELAY)
                    announced_offline = True
                # PortAudio кешира списъка устройства; реинициализацията хваща новo включени
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                self.online = False
                self.rms = 0.0
            self._stop_event.wait(REOPEN_DELAY)
        log.info("Аудио мониторът е спрян")


class WavWriterThread(threading.Thread):
    """Изпразва опашката с аудио блокове в 16-bit mono .wav файл."""

    def __init__(self, q: queue.Queue[np.ndarray], path: Path, samplerate: int):
        super().__init__(name="WavWriter", daemon=True)
        self.q = q
        self.path = path
        self.samplerate = samplerate
        self._done = threading.Event()
        self.frames_written = 0

    def finish(self) -> None:
        self._done.set()

    def run(self) -> None:
        try:
            with wave.open(str(self.path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.samplerate)
                while True:
                    try:
                        block = self.q.get(timeout=0.2)
                    except queue.Empty:
                        if self._done.is_set():
                            break
                        continue
                    pcm = (np.clip(block, -1.0, 1.0) * 32767.0).astype("<i2")
                    wf.writeframes(pcm.tobytes())
                    self.frames_written += len(pcm)
        except Exception:  # noqa: BLE001
            log.exception("Грешка при запис на %s", self.path)
