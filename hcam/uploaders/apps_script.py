"""Качване в Google Drive през ВАШ Google Apps Script линк — без Google Cloud и API ключове.

Скриптът (apps_script/Code.gs) приема клипа като base64 в JSON и го записва в Drive.
Лимит на Apps Script: ~50 MB на заявка -> клипове над ``max_mb`` се компресират до 480p.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import requests

from ..config import Config
from ..videotools import shrink_video
from .base import AuthRequiredError, Uploader, UploadError

log = logging.getLogger("hcam.gscript")


class AppsScriptUploader(Uploader):
    key = "gscript"
    host = "script.google.com"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.url = cfg.gdrive_script_url
        self.secret = cfg.gdrive_script_secret
        self.max_bytes = int(cfg.gdrive_script_max_mb * 1024 * 1024)

    def _call(self, payload: dict, timeout=(15, 600)) -> dict:
        if not self.url or not self.secret:
            raise AuthRequiredError("Липсва GDRIVE_SCRIPT_URL/SECRET в .env. Пуснете: python main.py --setup-script")
        payload = {"secret": self.secret, **payload}
        try:
            # Apps Script отговаря с 302 към script.googleusercontent.com; requests следва пренасочването.
            r = requests.post(self.url, json=payload, timeout=timeout)
        except requests.RequestException as e:
            raise UploadError(f"Мрежова грешка към Apps Script: {type(e).__name__}") from None
        if r.status_code == 429:
            raise UploadError("Apps Script: твърде много заявки", retry_after=300)
        try:
            data = r.json()
        except ValueError:
            snippet = r.text[:200].replace("\n", " ")
            if "accounts.google.com" in r.url or "ServiceLogin" in r.text:
                raise AuthRequiredError(
                    "Apps Script иска вход — при Deploy изберете 'Who has access: Anyone'") from None
            raise UploadError(f"Apps Script HTTP {r.status_code}: {snippet}") from None
        if not data.get("ok"):
            err = str(data.get("error", "неизвестна грешка"))
            if err == "bad secret":
                raise AuthRequiredError("Грешен таен ключ — SECRET в Code.gs и в .env не съвпадат")
            raise UploadError(f"Apps Script: {err}")
        return data

    def ping(self) -> str:
        """Проверява линка и ключа. Връща линк към Drive папката."""
        return self._call({"action": "ping"}, timeout=(15, 60)).get("folder", "")

    def upload(self, path: Path, date: str) -> str:
        send = path
        small: Path | None = None
        try:
            if path.stat().st_size > self.max_bytes:
                log.info("Клипът е > %.0f MB — компресирам копие за Apps Script", self.cfg.gdrive_script_max_mb)
                small = shrink_video(self.cfg, path, prefix="gs")
                if small is None or small.stat().st_size > self.max_bytes:
                    log.error("Клипът %s е твърде голям за Apps Script дори компресиран — остава само локално. "
                              "Намалете MAX_CLIP_SECONDS.", path.name)
                    return "too_large"
                send = small
            data = base64.b64encode(send.read_bytes()).decode("ascii")
            res = self._call({"name": path.name, "date": date, "mimeType": "video/mp4", "data": data})
            log.info("☁ Качено в Google Drive%s: %s", " (вече го има)" if res.get("existed") else "", path.name)
            return str(res.get("id", "ok"))
        finally:
            if small is not None:
                small.unlink(missing_ok=True)
