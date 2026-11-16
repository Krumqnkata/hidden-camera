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


def set_env_var(path: Path, key: str, value: str) -> None:
    """Записва/обновява KEY=VALUE в .env, като пази останалите редове."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    out, found = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ[key] = value


UPLOAD_ALIASES = {
    # Apps Script линк (без API ключове)
    "appsscript": "gscript", "script": "gscript", "gscript": "gscript", "link": "gscript",
    "drivelink": "gscript", "googledrivelink": "gscript",
    # Google Drive API (OAuth, Google Cloud)
    "googledriveapi": "gdrive", "gdriveapi": "gdrive", "driveapi": "gdrive", "gdrive": "gdrive",
    "googledrive": "gdrive", "drive": "gdrive",
    # Telegram
    "telegram": "telegram", "tg": "telegram",
}


def parse_upload_targets(method: str) -> list[str]:
    """'apps script, telegram' -> ['gscript', 'telegram']. 'none'/'' -> []."""
    targets: list[str] = []
    for part in re.split(r"[,+;]", method or ""):
        key = re.sub(r"[\s_\-]", "", part.lower())
        if key in ("", "none", "off", "local"):
            continue
        if key == "both":
            names = ["gdrive", "telegram"]
        elif key in UPLOAD_ALIASES:
            names = [UPLOAD_ALIASES[key]]
        else:
            raise ValueError(f"Непознат UPLOAD_METHOD: '{part.strip()}'")
        for n in names:
            if n not in targets:
                targets.append(n)
    return targets


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
    upload_method: str = "apps script"  # "apps script" | "google drive api" | "telegram" | списък със запетая | "none"
    gdrive_script_url: str = ""
    gdrive_script_secret: str = ""
    gdrive_script_max_mb: float = 30.0  # Apps Script приема ~50 MB заявка (base64 = +33%)
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
        return parse_upload_targets(self.upload_method)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    def ensure_dirs(self) -> None:
        for d in (self.recordings_dir, self.tmp_dir, self.state_dir, self.secrets_dir):
            d.mkdir(parents=True, exist_ok=True)
