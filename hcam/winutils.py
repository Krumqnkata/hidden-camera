"""Windows помощни функции: без конзолни прозорци, приоритет, будност на системата."""

from __future__ import annotations

import ctypes
import logging
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("hcam.win")

IS_WINDOWS = sys.platform == "win32"

# Флагове за subprocess, за да не се появяват конзолни прозорци под pythonw
CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
SUBPROCESS_FLAGS = (CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS) if IS_WINDOWS else 0

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def keep_system_awake(enable: bool = True) -> None:
    """Пречи на Windows да заспи, докато процесът работи. Екранът МОЖЕ да изгасне."""
    if not IS_WINDOWS:
        return
    try:
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED if enable else ES_CONTINUOUS
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:  # noqa: BLE001
        log.exception("SetThreadExecutionState не успя")


def find_ffmpeg(configured: str = "", base_dir: Path | None = None) -> str | None:
    """Търси ffmpeg: зададен път -> ./bin/ffmpeg.exe -> PATH -> imageio-ffmpeg."""
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    if base_dir:
        candidates.append(str(base_dir / "bin" / "ffmpeg.exe"))
    for c in candidates:
        if Path(c).is_file():
            return c
        found = shutil.which(c)
        if found:
            return found
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def run_hidden(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    """Стартира процес без прозорец и с понижен приоритет (щади вентилатора)."""
    return subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        creationflags=SUBPROCESS_FLAGS,
        timeout=timeout,
        check=False,
    )


def free_disk_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(path).free / (1024**3)
    except OSError:
        return float("inf")
