"""Вграждане на текст върху кадрите (име на камера, дата/час, тригер)."""

from __future__ import annotations

from datetime import datetime

import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX
TRIGGER_COLORS = {
    "MOTION": (0, 165, 255),  # оранжево (BGR)
    "AUDIO": (255, 200, 0),  # синьо-циан
    "BOTH": (0, 0, 255),  # червено
}


def _text(img: np.ndarray, text: str, org: tuple[int, int], scale: float, color, thickness: int) -> None:
    # Черен контур отдолу -> четим текст на всякакъв фон
    cv2.putText(img, text, org, FONT, scale, (0, 0, 0), thickness + 3, cv2.LINE_AA)
    cv2.putText(img, text, org, FONT, scale, color, thickness, cv2.LINE_AA)


def annotate(frame: np.ndarray, cam_name: str, trigger: str, now: datetime | None = None) -> np.ndarray:
    """Рисува върху ``frame`` (in-place) и го връща.

    Забележка: Hershey шрифтовете на OpenCV поддържат само ASCII — ползвайте
    латиница в имената на камерите.
    """
    now = now or datetime.now()
    h, w = frame.shape[:2]
    scale = max(0.45, w / 1280.0 * 0.8)
    th = max(1, int(round(scale * 2)))
    line_h = int(32 * scale) + 6
    pad = int(10 * scale) + 4

    _text(frame, cam_name, (pad, pad + line_h - 6), scale, (255, 255, 255), th)
    _text(frame, now.strftime("%Y-%m-%d"), (pad, pad + 2 * line_h - 6), scale, (255, 255, 255), th)
    _text(frame, now.strftime("%H:%M:%S"), (pad, pad + 3 * line_h - 6), scale, (255, 255, 255), th)

    label = f"[{trigger}]"
    (tw, _), _ = cv2.getTextSize(label, FONT, scale, th)
    _text(frame, label, (w - tw - pad, pad + line_h - 6), scale, TRIGGER_COLORS.get(trigger, (0, 0, 255)), th)

    # мигаща REC точка
    if now.second % 2 == 0:
        r = max(5, int(8 * scale))
        cy = pad + 2 * line_h - 6 - r
        cv2.circle(frame, (w - pad - r, cy), r, (0, 0, 255), -1, cv2.LINE_AA)
        (rw, _), _ = cv2.getTextSize("REC", FONT, scale * 0.8, th)
        _text(frame, "REC", (w - pad - 2 * r - rw - 8, cy + r), scale * 0.8, (0, 0, 255), th)
    return frame


def no_signal_frame(size: tuple[int, int], cam_name: str) -> np.ndarray:
    w, h = size
    img = np.zeros((h, w, 3), dtype=np.uint8)
    scale = max(0.6, w / 1280.0 * 1.4)
    msg = f"NO SIGNAL - {cam_name}"
    (tw, tht), _ = cv2.getTextSize(msg, FONT, scale, 2)
    _text(img, msg, ((w - tw) // 2, (h + tht) // 2), scale, (0, 0, 255), 2)
    return img
