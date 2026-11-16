"""Помощни ffmpeg операции (компресиране на копие за услуги с лимит на размера)."""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .winutils import find_ffmpeg, run_hidden


def shrink_video(cfg: Config, path: Path, height: int = 480, crf: int = 32, prefix: str = "small") -> Path | None:
    """Прави по-малко копие в recordings/_tmp. Връща пътя или None при грешка."""
    ffmpeg = find_ffmpeg(cfg.ffmpeg_path, cfg.base_dir)
    if not ffmpeg:
        return None
    out = cfg.tmp_dir / f"{prefix}_{path.stem}.mp4"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
           "-vf", f"scale=-2:{height}", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
           "-threads", str(cfg.ffmpeg_threads), "-c:a", "aac", "-b:a", "64k",
           "-movflags", "+faststart", str(out)]
    res = run_hidden(cmd, timeout=3600)
    if res.returncode != 0 or not out.is_file():
        out.unlink(missing_ok=True)
        return None
    return out
