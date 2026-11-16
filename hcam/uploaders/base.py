from __future__ import annotations

import socket
from abc import ABC, abstractmethod
from pathlib import Path


class UploadError(RuntimeError):
    """Временна грешка (мрежа, лимит) — ще се опита отново по-късно."""

    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class AuthRequiredError(UploadError):
    """Липсва/невалидна авторизация — изисква действие от потребителя."""


class Uploader(ABC):
    key: str = ""
    host: str = ""  # за бърза проверка на връзката

    def is_reachable(self, timeout: float = 5.0) -> bool:
        try:
            with socket.create_connection((self.host, 443), timeout=timeout):
                return True
        except OSError:
            return False

    @abstractmethod
    def upload(self, path: Path, date: str) -> str:
        """Качва файла. Връща идентификатор (file id / message id). Хвърля UploadError."""
