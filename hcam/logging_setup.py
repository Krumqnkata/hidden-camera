"""Логване в app.log (ротиращ файл) + безопасна работа под pythonw.exe."""

from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(threadName)-14s %(name)s: %(message)s"


def is_windowless() -> bool:
    """True, когато процесът е стартиран чрез pythonw.exe (няма конзола)."""
    return sys.stdout is None or sys.stderr is None or Path(sys.executable).stem.lower() == "pythonw"


def setup_logging(log_file: Path, level: str = "INFO") -> logging.Logger:
    # Под pythonw sys.stdout/sys.stderr са None -> библиотеки, които пишат в тях, крашват.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    # Конзолата/пайпът на Windows често е cp1252/cp866 -> кирилицата чупи логването
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(LOG_FORMAT)
    fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    if not is_windowless():
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    # По-тихи шумни библиотеки
    for noisy in ("googleapiclient.discovery_cache", "urllib3", "google_auth_httplib2", "httplib2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    log = logging.getLogger("hcam")

    def _excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return
        log.critical("Необработено изключение", exc_info=(exc_type, exc, tb))

    def _thread_excepthook(args: threading.ExceptHookArgs):
        log.critical(
            "Необработено изключение в нишка %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    return log
