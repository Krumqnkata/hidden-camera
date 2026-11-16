"""Single Instance lock чрез локален TCP сокет + малък команден канал (STATUS/STOP)."""

from __future__ import annotations

import json
import logging
import socket
import threading
from collections.abc import Callable

log = logging.getLogger("hcam.instance")

HOST = "127.0.0.1"


class AlreadyRunningError(RuntimeError):
    pass


class SingleInstance:
    """Заема 127.0.0.1:<port>. Втори процес не може да bind-не същия порт.

    Сокетът служи и като контролен канал:
      STATUS -> JSON със състоянието
      STOP   -> плавно спиране на приложението
    """

    def __init__(self, port: int):
        self.port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.on_stop: Callable[[], None] | None = None
        self.status_provider: Callable[[], dict] | None = None

    def acquire(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: забранява споделяне на порта
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind((HOST, self.port))
        except OSError as e:
            s.close()
            raise AlreadyRunningError(f"Портът {self.port} е зает — приложението вече работи.") from e
        s.listen(4)
        s.settimeout(1.0)
        self._sock = s
        self._thread = threading.Thread(target=self._serve, name="InstanceLock", daemon=True)
        self._thread.start()
        log.info("Single-instance lock е взет (127.0.0.1:%d)", self.port)

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                try:
                    conn.settimeout(2.0)
                    cmd = conn.recv(64).decode("ascii", "ignore").strip().upper()
                    if cmd == "STATUS":
                        data = self.status_provider() if self.status_provider else {}
                        conn.sendall(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))
                    elif cmd == "STOP":
                        conn.sendall(b"OK")
                        log.info("Получена команда STOP по локалния сокет")
                        if self.on_stop:
                            self.on_stop()
                    else:
                        conn.sendall(b"UNKNOWN")
                except OSError:
                    pass

    def release(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def send_command(port: int, command: str, timeout: float = 5.0) -> str | None:
    """Изпраща команда към работещата инстанция. Връща отговора или None."""
    try:
        with socket.create_connection((HOST, port), timeout=timeout) as s:
            s.sendall(command.encode("ascii"))
            s.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                b = s.recv(65536)
                if not b:
                    break
                chunks.append(b)
            return b"".join(chunks).decode("utf-8", "replace")
    except OSError:
        return None
