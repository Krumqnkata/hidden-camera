# 🎥 Hidden Camera

**Фонова система за сигурност за Windows** — превръща лаптопа (и по желание една USB камера) в охранителна камера с детекция на **движение + звук**, автоматичен запис и архивиране в **Google Drive** и/или **Telegram**.

![Python](https://img.shields.io/badge/python-3.12-blue) ![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d6) ![License](https://img.shields.io/badge/license-MIT-green)

## ✨ Възможности

| | |
|---|---|
| 🕵️ **Двойна детекция (OR)** | `cv2.absdiff` за движение на всяка камера **или** RMS амплитуда от микрофона |
| 🔋 **Щади батерията/вентилатора** | 5 FPS в покой, 15 FPS само по време на запис; детекция върху умалено 320px копие |
| 📷 **1 или 2 камери** | Вградена (index 0) по подразбиране; USB камера с един флаг (`ENABLE_SECOND_CAMERA = True`) — MJPG + 1280x720 |
| 🎬 **Синхронизиран запис** | видео с постоянни 15 FPS по стенния часовник + `.wav`; overlay с камера, дата, час и `[MOTION]`/`[AUDIO]`/`[BOTH]` |
| ⚙️ **Фоново обединяване** | ffmpeg (H.264 + AAC) в отделна нишка с понижен приоритет → `recordings/YYYY-MM-DD/` |
| ☁️ **Качване** | Google Drive (подпапки по дати) и/или Telegram (+ моментална снимка при тригер) |
| 🛡️ **Устойчивост** | USB камерата/микрофонът могат да се изключат и включат; без интернет клиповете се пазят и качват по-късно; възстановяване след срив |
| 👻 **Невидима работа** | `pythonw.exe`, без прозорци (и за ffmpeg), single-instance lock, автостарт + watchdog, пълен лог в `app.log` |

## 🏗️ Архитектура

```
main.py                    ← КОНФИГУРАЦИЯ + CLI
hcam/
├── controller.py          ← оркестратор: OR логика, старт/стоп на записа (10 Hz цикъл)
├── camera.py              ← CameraWorker (нишка на камера): кадри, absdiff, auto-reconnect
├── audio.py               ← AudioMonitor (RMS в реално време) + WavWriterThread
├── recorder.py            ← RecordingSession: по 1 .mp4 на камера + общ .wav
├── overlay.py             ← текст върху кадрите
├── merger.py              ← MergeWorker: ffmpeg mux, възстановяване на сирачета
├── upload_manager.py      ← опашка (на базата на файловата система) + retry/backoff + ретенция
├── uploaders/             ← gdrive.py, telegram.py
├── single_instance.py     ← lock на 127.0.0.1:47653 + команди STATUS/STOP
├── winutils.py            ← keep-awake, скрити процеси, ffmpeg търсене
└── logging_setup.py       ← app.log (ротация 5×5 MB), безопасно под pythonw
scripts/                   ← setup / windows-tweaks / start / stop / uninstall (.ps1)
```

```
 Cam0 ─┐                     ┌─► VideoWriter cam0 ─┐
 Cam1 ─┼─► Controller (OR) ──┼─► VideoWriter cam1 ─┼─► MergeWorker (ffmpeg) ─► recordings/YYYY-MM-DD/*.mp4 ─► UploadManager ─► Drive / Telegram
 Mic  ─┘   +15 s post-trigger└─► WavWriter ────────┘
```

## 🚀 Бърз старт

```bat
git clone https://github.com/<you>/hidden-camera.git
cd hidden-camera
setup.bat
```

`setup.bat` прави всичко:

1. намира/инсталира Python 3.12 (winget),
2. създава `.venv` и инсталира `requirements.txt`,
3. създава `.env` от `.env.example`,
4. разрешава достъп до камера/микрофон за десктоп приложения,
5. проверява ffmpeg (идва автоматично с `imageio-ffmpeg`),
6. пуска Google вход, ако има `secrets/credentials.json`,
7. **с UAC (администратор):** powercfg настройки + задача в Task Scheduler,
8. стартира приложението във фонов режим.

Опции: `setup.bat -BatteryToo` (настройките важат и на батерия), `-NoAdmin`, `-NoStart`.

| Команда | Действие |
|---|---|
| `start.bat` | стартира (през Task Scheduler или директно с `pythonw`) |
| `stop.bat` | спира плавно (watchdog-ът го пуска отново до 5 мин) |
| `stop.bat -Disable` | спира и изключва автостарта |
| `status.bat` | JSON статус + последните редове от лога |
| `scripts\uninstall.ps1` | маха задачата и връща капака към Sleep |

```bat
.venv\Scripts\python main.py --list-devices   :: ID-та на камери и микрофони
.venv\Scripts\python main.py --calibrate 30   :: % движение и RMS на живо — за настройка на праговете
.venv\Scripts\python main.py --auth-gdrive    :: (пре)свързване с Google Drive
.venv\Scripts\python main.py --test-telegram  :: тестово съобщение
```

## ⚙️ Конфигурация

Всичко е в началото на [`main.py`](main.py). Тайните са в `.env`.

| Настройка | По подразбиране | Описание |
|---|---|---|
| `ENABLE_SECOND_CAMERA` | `False` | `True` → втора камера в отделна нишка, MJPG + 1280x720 |
| `PRIMARY_CAM_INDEX` / `SECONDARY_CAM_INDEX` | `0` / `1` | индекси на камерите (`--list-devices`) |
| `AUDIO_DEVICE_INDEX` | `None` | `None` = микрофон по подразбиране |
| `MOTION_THRESHOLD` | `25` | мин. промяна на яркостта на пиксел (0–255) |
| `MOTION_MIN_AREA_PERCENT` | `0.5` | % от кадъра, който трябва да се промени |
| `AUDIO_THRESHOLD` | `0.02` | RMS праг (0–1) |
| `POST_TRIGGER_SECONDS` | `15` | запис още N сек след последния тригер |
| `MAX_CLIP_SECONDS` | `600` | дълги събития се режат на части |
| `UPLOAD_METHOD` | `"google drive"` | `"google drive"` / `"telegram"` / `"both"` / `"none"` |
| `GDRIVE_FOLDER` (.env) | празно | линк или ID на папка; празно → авто-папка "Hidden Camera" |
| `DELETE_LOCAL_AFTER_UPLOAD` | `False` | трие локалния файл след успешно качване |
| `LOCAL_RETENTION_DAYS` | `30` | трие **качени** клипове, по-стари от N дни |
| `MIN_FREE_DISK_GB` | `2.0` | при малко място трие най-старите **качени** клипове |

> 💡 Пуснете `--calibrate`, оставете стаята празна и тиха → вижте фоновите стойности → сложете праговете малко над тях.

## ☁️ Google Drive

1. Отворете [Google Cloud Console](https://console.cloud.google.com/) → създайте проект (напр. *hidden-camera*).
2. **APIs & Services → Library** → включете **Google Drive API**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform):
   * User type: **External**, попълнете име и имейл.
   * **Audience → Publish app → In production.** ⚠️ Ако остане в *Testing*, токенът изтича на всеки **7 дни** и качването спира.
4. **Credentials → Create credentials → OAuth client ID → Application type: Desktop app**.
5. Свалете JSON файла и го запазете като **`secrets/credentials.json`**.
6. Пуснете:
   ```bat
   .venv\Scripts\python main.py --auth-gdrive
   ```
   Отваря се браузър → вход → „Google hasn't verified this app“ → **Advanced → Go to … (unsafe)** (това е вашето собствено приложение) → Allow.
   В конзолата ще се отпечата **линкът към папката**, в която ще се качват клиповете (записва се и в `state/gdrive_folder_link.txt`).

**Коя папка?**

* **Препоръчително:** оставете `GDRIVE_FOLDER` празно. Приложението създава `Hidden Camera/YYYY-MM-DD/` в My Drive и иска само права `drive.file` (вижда единствено файловете, които само е създало).
* **Собствена папка:** сложете в `.env` `GDRIVE_FOLDER=https://drive.google.com/drive/folders/<ID>` и пуснете `--auth-gdrive` отново. Това изисква пълни права `drive` (Google не позволява на `drive.file` да пише в папки, създадени от вас ръчно).

## ✈️ Telegram (по желание)

1. В Telegram пишете на **@BotFather** → `/newbot` → копирайте токена.
2. Пишете каквото и да е на новия си бот.
3. Отворете `https://api.telegram.org/bot<TOKEN>/getUpdates` → намерете `"chat":{"id": ...}`.
4. В `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=123456789
   ```
5. `.venv\Scripts\python main.py --test-telegram`

Ако Telegram е настроен, при всеки тригер идва **моментална снимка**, дори `UPLOAD_METHOD` да е само Google Drive. Клипове над 48 MB (лимит на Bot API 50 MB) се компресират автоматично до 480p.

## 💻 Windows: капак, екран, sleep

`setup.bat` го прави автоматично (`scripts/windows-tweaks.ps1`). Ръчно:

**Control Panel → Hardware and Sound → Power Options → Choose what closing the lid does**
* *When I close the lid* → **Do nothing** (Plugged in; и On battery, ако искате)

**Settings → System → Power & battery → Screen and sleep**
* *Turn off my screen after* → **1 minute** (екранът може да е изгасен — записът продължава)
* *Put my device to sleep after* → **Never**

**Еквивалентни команди (администратор):**
```bat
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 1
powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
powercfg /setactive SCHEME_CURRENT
```

Допълнително:
* Приложението извиква `SetThreadExecutionState`, така че Windows **не заспива**, докато то работи, но екранът изгасва нормално.
* **Заключване (Win+L) е ОК.** Отписване (Sign out) спира приложението — задачата е *Interactive*, защото Windows дава камера/микрофон само в потребителската сесия.
* Лаптопи с **Modern Standby** (`powercfg /a` показва *S0 Low Power Idle*): дръжте ги на захранване.
* **Ако затворите капака, вградената камера вижда само клавиатурата** 🙂 — за затворен капак използвайте USB камера (`ENABLE_SECOND_CAMERA = True`) или оставете капака отворен с изгасен екран.
* Изключете Windows Update рестартите в активните часове (*Settings → Windows Update → Advanced → Active hours*), за да не прекъсват наблюдението.

## 🗂️ Файлове по време на работа

```
recordings/
├── _tmp/                      суровите .raw.mp4/.raw.wav + session .json по време на запис
└── 2026-11-16/
    ├── 2026-11-16_143022_cam0_Laptop.mp4
    └── raw_failed/            (ако ffmpeg не успее — нищо не се губи)
state/
├── uploads.json               кои клипове къде са качени
├── gdrive.json                ID на Drive папките
└── gdrive_folder_link.txt
secrets/                       credentials.json, token.json  (в .gitignore)
app.log                        ротиращ лог (5 × 5 MB)
```

## 🩺 Отстраняване на проблеми

| Симптом | Решение |
|---|---|
| `Портът 47653 е зает` в лога | приложението вече работи (`status.bat`) |
| Камерата е `offline` | друга програма (Teams/Zoom) я държи; проверете *Privacy → Camera*; опитайте `CAMERA_BACKEND = "MSMF"` |
| Черно видео с USB камера | пробвайте друг USB порт (2 камери на един хъб = недостиг на честотна лента) |
| Много фалшиви тригери | увеличете `MOTION_MIN_AREA_PERCENT` / `AUDIO_THRESHOLD` (вижте `--calibrate`) |
| `Google токенът е невалиден` | приложението е в *Testing* режим → Publish app → `--auth-gdrive` |
| Нищо не се качва | `status.bat` → `uploads.pending`; `app.log` съдържа причината |

## ⚖️ Правна бележка

Използвайте само във **ваше собствено жилище/пространство**. Записът на хора (особено на **звук**) без тяхно знание може да е незаконен според GDPR и местното законодателство. Уведомете домакинството/гостите, че има камера.

## 📄 Лиценз

[MIT](LICENSE)
