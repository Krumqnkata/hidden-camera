"""Конфигурационен обект, който се попълва от константите в main.py и от .env."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Минимален .env парсър (KEY=VALUE), без външни зависимости.

    Не презаписва променливи, които вече са зададени в средата.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_gdrive_folder_id(value: str) -> str:
    """Приема ID или пълен линк към Google Drive папка и връща ID-то."""
    value = (value or "").strip()
    if not value:
        return ""
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", value)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", value)
    if m:
        return m.group(1)
    return value


@dataclass(frozen=True)
class CameraSpec:
    index: int
    name: str
    force_mjpg: bool = False
    resolution: tuple[int, int] | None = None


@dataclass
class Config:
    base_dir: Path

    # --- Камери ---
    cameras: list[CameraSpec] = field(default_factory=list)
    camera_backend: str = "DSHOW"  # DSHOW | MSMF | ANY

    # --- Аудио ---
    audio_enabled: bool = True
    audio_device_index: int | None = None
    audio_samplerate: int | None = None  # None = подразбиращата се за устройството

    # --- Детекция ---
    motion_threshold: int = 25
    motion_min_area_percent: float = 0.5
    audio_threshold: float = 0.02
    idle_fps: float = 5.0
    record_fps: float = 15.0
    camera_warmup_seconds: float = 3.0
    trigger_hold_seconds: float = 1.0

    # --- Запис ---
    post_trigger_seconds: float = 15.0
    max_clip_seconds: float = 600.0

    # --- ffmpeg ---
    ffmpeg_path: str = ""
    video_crf: int = 26
    video_preset: str = "veryfast"
    ffmpeg_threads: int = 2

    # --- Качване ---
    upload_method: str = "gdrive"  # gdrive | telegram | both | none
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_snapshot_on_trigger: bool = True
    telegram_max_mb: float = 48.0
    gdrive_folder: str = ""  # ID или линк; празно = приложението създава своя папка
    gdrive_root_folder_name: str = "Hidden Camera"
    gdrive_date_subfolders: bool = True
    upload_scan_interval: float = 60.0

    # --- Локално съхранение ---
    delete_local_after_upload: bool = False
    local_retention_days: int = 30
    min_free_disk_gb: float = 2.0

    # --- Система ---
    single_instance_port: int = 47653
    keep_system_awake: bool = True
    log_level: str = "INFO"

    # ---------- производни пътища ----------
    @property
    def recordings_dir(self) -> Path:
        return self.base_dir / "recordings"

    @property
    def tmp_dir(self) -> Path:
        return self.recordings_dir / "_tmp"

    @property
    def state_dir(self) -> Path:
        return self.base_dir / "state"

    @property
    def secrets_dir(self) -> Path:
        return self.base_dir / "secrets"

    @property
    def log_file(self) -> Path:
        return self.base_dir / "app.log"

    @property
    def gdrive_folder_id(self) -> str:
        return parse_gdrive_folder_id(self.gdrive_folder)

    @property
    def upload_targets(self) -> list[str]:
        m = (self.upload_method or "none").strip().lower().replace(" ", "")
        aliases = {
            "googledrive": "gdrive",
            "drive": "gdrive",
            "gdrive": "gdrive",
            "telegram": "telegram",
            "tg": "telegram",
            "both": "both",
            "none": "none",
            "": "none",
        }
        m = aliases.get(m, m)
        if m == "both":
            return ["gdrive", "telegram"]
        if m in ("gdrive", "telegram"):
            return [m]
        return []

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    def ensure_dirs(self) -> None:
        for d in (self.recordings_dir, self.tmp_dir, self.state_dir, self.secrets_dir):
            d.mkdir(parents=True, exist_ok=True)
