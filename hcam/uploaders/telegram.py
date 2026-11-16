"""Изпращане на клипове/снимки чрез Telegram Bot API."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import requests

from ..config import Config
from ..winutils import find_ffmpeg, run_hidden
from .base import AuthRequiredError, Uploader, UploadError

log = logging.getLogger("hcam.telegram")

CLIP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})_cam(\d+)_(.+)\.mp4$")


class TelegramUploader(Uploader):
    key = "telegram"
    host = "api.telegram.org"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.token = cfg.telegram_bot_token
        self.chat_id = cfg.telegram_chat_id
        self.base = f"https://api.telegram.org/bot{self.token}/"
        self.max_bytes = int(cfg.telegram_max_mb * 1024 * 1024)

    def _redact(self, text: str) -> str:
        # requests включва URL-а (с токена!) в съобщенията за грешки — никога в лога
        return text.replace(self.token, "<TOKEN>") if self.token else text

    def _post(self, method: str, data: dict, files: dict | None = None, timeout=(15, 600)) -> dict:
        try:
            r = requests.post(self.base + method, data=data, files=files, timeout=timeout)
        except requests.RequestException as e:
            raise UploadError(self._redact(f"Telegram мрежова грешка: {e}")) from None
        try:
            payload = r.json()
        except ValueError:
            payload = {"ok": False, "description": r.text[:200]}
        if r.status_code == 429:
            retry = float(payload.get("parameters", {}).get("retry_after", 30))
            raise UploadError(f"Telegram rate limit, изчакване {retry:.0f} с", retry_after=retry)
        if r.status_code in (401, 404):
            raise AuthRequiredError("Невалиден TELEGRAM_BOT_TOKEN")
        if r.status_code == 400 and "chat not found" in str(payload.get("description", "")).lower():
            raise AuthRequiredError("Невалиден TELEGRAM_CHAT_ID (или не сте писали на бота)")
        if not payload.get("ok"):
            raise UploadError(self._redact(f"Telegram {r.status_code}: {payload.get('description')}"))
        return payload["result"]

    @staticmethod
    def caption_for(path: Path) -> str:
        m = CLIP_RE.match(path.name)
        if not m:
            return f"🎥 {path.name}"
        date, hh, mm, ss, _idx, cam = m.groups()
        return f"🎥 {cam} | {date} {hh}:{mm}:{ss}"

    def _shrink(self, path: Path) -> Path | None:
        ffmpeg = find_ffmpeg(self.cfg.ffmpeg_path, self.cfg.base_dir)
        if not ffmpeg:
            return None
        out = self.cfg.tmp_dir / f"tg_{path.stem}.mp4"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
               "-vf", "scale=-2:480", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
               "-threads", str(self.cfg.ffmpeg_threads), "-c:a", "aac", "-b:a", "64k",
               "-movflags", "+faststart", str(out)]
        res = run_hidden(cmd, timeout=3600)
        if res.returncode != 0 or not out.is_file():
            out.unlink(missing_ok=True)
            return None
        return out

    def upload(self, path: Path, date: str) -> str:  # noqa: ARG002
        caption = self.caption_for(path)
        send = path
        shrunk: Path | None = None
        try:
            if path.stat().st_size > self.max_bytes:
                log.info("Клипът е > %.0f MB — компресирам копие за Telegram", self.cfg.telegram_max_mb)
                shrunk = self._shrink(path)
                if shrunk is None or shrunk.stat().st_size > self.max_bytes:
                    self.send_message(f"{caption}\n⚠ Клипът е твърде голям за Telegram — запазен е локално.")
                    return "too_large"
                send = shrunk
            with open(send, "rb") as f:
                res = self._post(
                    "sendVideo",
                    data={"chat_id": self.chat_id, "caption": caption, "supports_streaming": "true"},
                    files={"video": (path.name, f, "video/mp4")},
                )
            log.info("✈ Изпратено в Telegram: %s", path.name)
            return str(res.get("message_id", "ok"))
        finally:
            if shrunk is not None:
                shrunk.unlink(missing_ok=True)

    # ---------------- известия (best-effort) ----------------
    def send_message(self, text: str) -> None:
        self._post("sendMessage", data={"chat_id": self.chat_id, "text": text}, timeout=(10, 30))

    def send_photo(self, jpeg: bytes, caption: str) -> None:
        self._post("sendPhoto", data={"chat_id": self.chat_id, "caption": caption},
                   files={"photo": ("snapshot.jpg", jpeg, "image/jpeg")}, timeout=(10, 60))
