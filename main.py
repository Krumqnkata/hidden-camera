"""
Hidden Camera — фонова система за сигурност за Windows
=======================================================
Motion (OpenCV) + Audio (sounddevice) детекция с OR логика, синхронизиран запис,
фоново обединяване с ffmpeg и качване в Google Drive / Telegram.

Употреба:
    pythonw main.py                 # фонов режим (без прозорец)
    python  main.py                 # с конзолен изход
    python  main.py --setup-script  # свързване с Google Drive чрез линк (без API ключове)
    python  main.py --auth-gdrive   # (разширено) Google Drive API авторизация
    python  main.py --test-telegram # тестово съобщение в Telegram
    python  main.py --list-devices  # списък с камери и микрофони
    python  main.py --calibrate 30  # показва % движение и RMS на живо (за прагове)
    python  main.py --status        # състояние на работещата инстанция
    python  main.py --stop          # плавно спиране на работещата инстанция

Тайните (Telegram токен, Drive папка) са в .env (вижте .env.example).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from hcam.config import load_dotenv  # noqa: E402

load_dotenv(BASE_DIR / ".env")

# ╔══════════════════════════════════════════════════════════════════════╗
# ║                         К О Н Ф И Г У Р А Ц И Я                        ║
# ╚══════════════════════════════════════════════════════════════════════╝

# ---- Камери ----------------------------------------------------------------
ENABLE_SECOND_CAMERA = False  # True -> стартира и USB камерата в отделна нишка
PRIMARY_CAM_INDEX = 0  # вградената камера на лаптопа
SECONDARY_CAM_INDEX = 1  # външна USB камера
PRIMARY_CAM_NAME = "Laptop"  # само латиница (OpenCV шрифтът е ASCII)
SECONDARY_CAM_NAME = "USB"
DUAL_CAM_RESOLUTION = (1280, 720)  # при 2 камери: MJPG + 1280x720 (пести USB честотна лента)
DUAL_CAM_FORCE_ON_PRIMARY = True  # прилага MJPG/720p и върху вградената камера в двоен режим
PRIMARY_CAM_RESOLUTION = None  # при една камера: None = подразбиращата се, или напр. (1280, 720)
CAMERA_BACKEND = "DSHOW"  # DSHOW (препоръчително) | MSMF | ANY

# ---- Аудио -----------------------------------------------------------------
ENABLE_AUDIO = True
AUDIO_DEVICE_INDEX = None  # None = микрофонът по подразбиране; вижте --list-devices

# ---- Прагове ---------------------------------------------------------------
MOTION_THRESHOLD = 25  # мин. промяна на яркостта на пиксел (0-255) при cv2.absdiff
MOTION_MIN_AREA_PERCENT = 0.5  # % от кадъра, който трябва да се промени
AUDIO_THRESHOLD = 0.05  # RMS амплитуда (0.0 - 1.0); тиха стая на този лаптоп е ~0.01-0.03

# ---- Запис -----------------------------------------------------------------
IDLE_FPS = 5  # FPS в режим на изчакване (щади CPU/вентилатора)
RECORD_FPS = 15  # FPS на записа
POST_TRIGGER_SECONDS = 15  # колко секунди продължава записът след последния тригер
MAX_CLIP_SECONDS = 300  # дълги събития се разделят на клипове по 5 мин (държи ги под лимита за качване)

# ---- Качване ---------------------------------------------------------------
# "apps script"      -> Google Drive чрез ВАШ линк (без API ключове)  <- препоръчително
# "google drive api" -> Google Drive чрез Google Cloud OAuth (разширено)
# "telegram"         -> Telegram бот
# "none"             -> само локално
# Може и няколко: "apps script, telegram"
UPLOAD_METHOD = "apps script"
GDRIVE_SCRIPT_URL = os.getenv("GDRIVE_SCRIPT_URL", "")  # попълва се от --setup-script
GDRIVE_SCRIPT_SECRET = os.getenv("GDRIVE_SCRIPT_SECRET", "")
# Само за "google drive api": линк/ID на папка. Празно -> създава "Hidden Camera" в My Drive.
GDRIVE_FOLDER = os.getenv("GDRIVE_FOLDER", "")
GDRIVE_DATE_SUBFOLDERS = True  # подпапки YYYY-MM-DD и в Drive
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_SNAPSHOT_ON_TRIGGER = True  # моментална снимка в Telegram при тригер (ако е настроен)

# ---- Локално съхранение ----------------------------------------------------
DELETE_LOCAL_AFTER_UPLOAD = False
LOCAL_RETENTION_DAYS = 30  # изтрива КАЧЕНИ клипове по-стари от N дни (0 = никога)
MIN_FREE_DISK_GB = 2.0  # при по-малко място трие най-старите КАЧЕНИ клипове

# ---- Система ---------------------------------------------------------------
SINGLE_INSTANCE_PORT = 47653
KEEP_SYSTEM_AWAKE = True  # не позволява sleep (екранът все пак може да изгасне)
FFMPEG_PATH = ""  # празно = автоматично (PATH -> bin/ffmpeg.exe -> imageio-ffmpeg)
LOG_LEVEL = "INFO"  # DEBUG за подробности

# ════════════════════════════════════════════════════════════════════════════


def build_config():
    from hcam.config import CameraSpec, Config

    cams = []
    if ENABLE_SECOND_CAMERA:
        cams.append(CameraSpec(
            PRIMARY_CAM_INDEX, PRIMARY_CAM_NAME,
            force_mjpg=DUAL_CAM_FORCE_ON_PRIMARY,
            resolution=DUAL_CAM_RESOLUTION if DUAL_CAM_FORCE_ON_PRIMARY else PRIMARY_CAM_RESOLUTION,
        ))
        cams.append(CameraSpec(SECONDARY_CAM_INDEX, SECONDARY_CAM_NAME,
                               force_mjpg=True, resolution=DUAL_CAM_RESOLUTION))
    else:
        cams.append(CameraSpec(PRIMARY_CAM_INDEX, PRIMARY_CAM_NAME, resolution=PRIMARY_CAM_RESOLUTION))

    cfg = Config(
        base_dir=BASE_DIR,
        cameras=cams,
        camera_backend=CAMERA_BACKEND,
        audio_enabled=ENABLE_AUDIO,
        audio_device_index=AUDIO_DEVICE_INDEX,
        motion_threshold=MOTION_THRESHOLD,
        motion_min_area_percent=MOTION_MIN_AREA_PERCENT,
        audio_threshold=AUDIO_THRESHOLD,
        idle_fps=IDLE_FPS,
        record_fps=RECORD_FPS,
        post_trigger_seconds=POST_TRIGGER_SECONDS,
        max_clip_seconds=MAX_CLIP_SECONDS,
        ffmpeg_path=FFMPEG_PATH,
        upload_method=UPLOAD_METHOD,
        gdrive_script_url=GDRIVE_SCRIPT_URL,
        gdrive_script_secret=GDRIVE_SCRIPT_SECRET,
        telegram_bot_token=TELEGRAM_BOT_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID,
        telegram_snapshot_on_trigger=TELEGRAM_SNAPSHOT_ON_TRIGGER,
        gdrive_folder=GDRIVE_FOLDER,
        gdrive_date_subfolders=GDRIVE_DATE_SUBFOLDERS,
        delete_local_after_upload=DELETE_LOCAL_AFTER_UPLOAD,
        local_retention_days=LOCAL_RETENTION_DAYS,
        min_free_disk_gb=MIN_FREE_DISK_GB,
        single_instance_port=SINGLE_INSTANCE_PORT,
        keep_system_awake=KEEP_SYSTEM_AWAKE,
        log_level=LOG_LEVEL,
    )
    cfg.ensure_dirs()
    return cfg


# ─────────────────────────────── команди ────────────────────────────────


def cmd_run(cfg) -> int:
    import logging
    import signal

    from hcam import __version__
    from hcam.logging_setup import setup_logging
    from hcam.single_instance import AlreadyRunningError, SingleInstance

    log = setup_logging(cfg.log_file, cfg.log_level)
    lock = SingleInstance(cfg.single_instance_port)
    try:
        lock.acquire()
    except AlreadyRunningError as e:
        log.warning("%s Изход.", e)
        return 1

    log.info("=" * 60)
    log.info("Hidden Camera v%s стартира (PID %d, %s)", __version__, os.getpid(), sys.executable)

    try:
        from hcam.controller import SecuritySystem

        system = SecuritySystem(cfg)
    except Exception:
        logging.getLogger("hcam").exception("Фатална грешка при инициализация")
        lock.release()
        return 2

    lock.on_stop = system.request_stop
    lock.status_provider = system.status

    def _sig(_signum, _frame):
        system.request_stop()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _sig)

    try:
        system.run()
    finally:
        lock.release()
    return 0


def cmd_setup_script(cfg, only_if_needed: bool = False) -> int:
    """Води потребителя стъпка по стъпка: таен ключ -> Code.gs -> Deploy -> линк -> проверка."""
    import secrets as pysecrets
    import time
    import webbrowser

    from hcam.config import set_env_var
    from hcam.uploaders import AppsScriptUploader, UploadError

    if only_if_needed and ("gscript" not in cfg.upload_targets or (cfg.gdrive_script_url and cfg.gdrive_script_secret)):
        print("Google Drive линкът е настроен (или не се ползва) — пропускам.")
        return 0

    env_file = BASE_DIR / ".env"
    if not env_file.exists() and (BASE_DIR / ".env.example").exists():
        env_file.write_text((BASE_DIR / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")

    secret = cfg.gdrive_script_secret or pysecrets.token_urlsafe(24)
    set_env_var(env_file, "GDRIVE_SCRIPT_SECRET", secret)
    cfg.gdrive_script_secret = secret

    template = (BASE_DIR / "apps_script" / "Code.gs").read_text(encoding="utf-8")
    code_file = cfg.secrets_dir / "Code.gs"
    code_file.write_text(template.replace("__HCAM_SECRET__", secret), encoding="utf-8")

    print(
        "\n=== Свързване с Google Drive (без API ключове) ===\n\n"
        "Отварям Notepad с кода и браузър със script.google.com.\n\n"
        "  1. В Notepad: Ctrl+A, Ctrl+C (копирай целия код).\n"
        "  2. В браузъра: изтрий всичко в редактора, Ctrl+V, после Ctrl+S.\n"
        "  3. Горе вдясно: Deploy -> New deployment -> зъбчето -> Web app.\n"
        "        Execute as:      Me\n"
        "        Who has access:  Anyone\n"
        "     -> Deploy -> Authorize access -> избери акаунта ->\n"
        "        Advanced -> Go to ... (unsafe) -> Allow   (това е твоят собствен скрипт)\n"
        "  4. Копирай 'Web app URL' (завършва на /exec) и го постави тук.\n"
    )
    time.sleep(1)
    try:
        os.startfile(str(code_file))  # type: ignore[attr-defined]  # отваря в Notepad / редактор
    except (AttributeError, OSError):
        print(f"Отворете ръчно: {code_file}")
    webbrowser.open("https://script.new")

    while True:
        url = input("Web app URL: ").strip().strip('"')
        if not url:
            print("Отказано. Пуснете отново по-късно: python main.py --setup-script")
            return 1
        if not (url.startswith("https://script.google.com/") and url.rstrip("/").endswith("/exec")):
            print("  Линкът трябва да започва с https://script.google.com/ и да завършва на /exec. Опитай пак.")
            continue
        cfg.gdrive_script_url = url
        try:
            folder = AppsScriptUploader(cfg).ping()
        except UploadError as e:
            print(f"  ✖ Проверката не мина: {e}\n  Провери стъпка 3 и опитай пак (или Enter за изход).")
            continue
        set_env_var(env_file, "GDRIVE_SCRIPT_URL", url)
        (cfg.state_dir / "gdrive_folder_link.txt").write_text(folder, encoding="utf-8")
        print(f"\n✔ Готово! Клиповете ще се качват в:\n  {folder}\n")
        return 0


def cmd_auth_gdrive(cfg) -> int:
    from hcam.logging_setup import setup_logging
    from hcam.uploaders import GDriveUploader

    setup_logging(cfg.log_file, cfg.log_level)
    up = GDriveUploader(cfg)
    link = up.authorize_interactive()
    print(f"\n✔ Google Drive е свързан. Клиповете ще се качват тук:\n  {link}\n")
    return 0


def cmd_test_telegram(cfg) -> int:
    from hcam.uploaders import TelegramUploader

    if not cfg.telegram_configured:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID липсват в .env")
        return 1
    TelegramUploader(cfg).send_message("✅ Hidden Camera: тестово съобщение — Telegram работи.")
    print("✔ Изпратено.")
    return 0


def cmd_list_devices(cfg) -> int:
    import cv2

    print("== Микрофони (използвайте ID за AUDIO_DEVICE_INDEX) ==")
    try:
        import sounddevice as sd

        default_in = sd.default.device[0]
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                mark = "  <- по подразбиране" if i == default_in else ""
                print(f"  [{i:2d}] {d['name']}  ({int(d['default_samplerate'])} Hz){mark}")
    except Exception as e:  # noqa: BLE001
        print(f"  грешка: {e}")

    print("\n== Камери (проверка на индекси 0-4) ==")
    backend = {"DSHOW": cv2.CAP_DSHOW, "MSMF": cv2.CAP_MSMF}.get(cfg.camera_backend.upper(), cv2.CAP_ANY)
    for idx in range(5):
        cap = cv2.VideoCapture(idx, backend)
        ok, frame = cap.read() if cap.isOpened() else (False, None)
        if ok and frame is not None:
            print(f"  [{idx}] OK  {frame.shape[1]}x{frame.shape[0]}")
        cap.release()
    return 0


def cmd_calibrate(cfg, seconds: float) -> int:
    import threading
    import time

    from hcam.audio import AudioMonitor
    from hcam.camera import CameraWorker
    from hcam.logging_setup import setup_logging

    setup_logging(cfg.log_file, "WARNING")
    stop = threading.Event()
    cams = [CameraWorker(s, cfg, stop) for s in cfg.cameras]
    audio = AudioMonitor(cfg, stop) if cfg.audio_enabled else None
    for c in cams:
        c.start()
    if audio:
        audio.start()
    print(f"Калибриране {seconds:.0f} с. Праг движение: {cfg.motion_min_area_percent}% "
          f"(пиксел > {cfg.motion_threshold}), праг звук: RMS {cfg.audio_threshold}\n")
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            now = time.monotonic()
            parts = []
            for c in cams:
                flag = "M" if c.motion_active(now) else " "
                parts.append(f"{c.spec.name}: {c.motion_score:5.2f}% {flag}" if c.online else f"{c.spec.name}: offline")
            if audio:
                flag = "A" if audio.audio_active(now) else " "
                bar = "#" * min(30, int(audio.rms / max(cfg.audio_threshold, 1e-6) * 10))
                parts.append(f"RMS {audio.rms:.4f} {flag} {bar}")
            print("  |  ".join(parts).ljust(110), end="\r", flush=True)
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    stop.set()
    print()
    return 0


def cmd_control(cfg, command: str) -> int:
    import json

    from hcam.single_instance import send_command

    resp = send_command(cfg.single_instance_port, command)
    if resp is None:
        print("Приложението НЕ работи.")
        return 1
    if command == "STATUS":
        try:
            print(json.dumps(json.loads(resp), ensure_ascii=False, indent=2))
        except ValueError:
            print(resp)
    else:
        print("Изпратена команда за спиране." if resp == "OK" else resp)
    return 0


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Hidden Camera — Motion + Audio security recorder")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--setup-script", action="store_true", help="свързване с Google Drive чрез линк (без API)")
    g.add_argument("--auth-gdrive", action="store_true", help="(разширено) Google Drive API авторизация")
    g.add_argument("--test-telegram", action="store_true", help="тестово Telegram съобщение")
    g.add_argument("--list-devices", action="store_true", help="списък камери/микрофони")
    g.add_argument("--calibrate", type=float, nargs="?", const=30.0, metavar="SEC", help="live стойности на сензорите")
    g.add_argument("--status", action="store_true", help="статус на работещата инстанция")
    g.add_argument("--stop", action="store_true", help="спира работещата инстанция")
    p.add_argument("--if-needed", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    cfg = build_config()
    if args.setup_script:
        return cmd_setup_script(cfg, only_if_needed=args.if_needed)
    if args.auth_gdrive:
        return cmd_auth_gdrive(cfg)
    if args.test_telegram:
        return cmd_test_telegram(cfg)
    if args.list_devices:
        return cmd_list_devices(cfg)
    if args.calibrate is not None:
        return cmd_calibrate(cfg, args.calibrate)
    if args.status:
        return cmd_control(cfg, "STATUS")
    if args.stop:
        return cmd_control(cfg, "STOP")
    return cmd_run(cfg)


if __name__ == "__main__":
    # Конзолата на Windows по подразбиране не е UTF-8 -> кирилицата/емоджитата се чупят
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    sys.exit(main(sys.argv[1:]))
