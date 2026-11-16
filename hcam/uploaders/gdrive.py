"""Качване в Google Drive чрез OAuth (Desktop app) и resumable upload."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from ..config import Config
from .base import AuthRequiredError, Uploader, UploadError

log = logging.getLogger("hcam.gdrive")

SCOPE_FILE = "https://www.googleapis.com/auth/drive.file"  # само файлове, създадени от приложението
SCOPE_FULL = "https://www.googleapis.com/auth/drive"  # нужно, ако качваме в ВАША съществуваща папка
FOLDER_MIME = "application/vnd.google-apps.folder"
CHUNK = 8 * 1024 * 1024


def folder_link(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


class GDriveUploader(Uploader):
    key = "gdrive"
    host = "www.googleapis.com"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.credentials_file = cfg.secrets_dir / "credentials.json"
        self.token_file = cfg.secrets_dir / "token.json"
        self.state_file = cfg.state_dir / "gdrive.json"
        self.user_folder_id = cfg.gdrive_folder_id
        self.scopes = [SCOPE_FULL] if self.user_folder_id else [SCOPE_FILE]
        self._service = None
        self._root_id: str | None = None
        self._date_folders: dict[str, str] = {}
        self._lock = threading.Lock()
        self._load_state()

    # ---------------- state ----------------
    def _load_state(self) -> None:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("user_folder_id", "") == self.user_folder_id:
            self._root_id = data.get("root_folder_id")
            self._date_folders = data.get("date_folders", {})

    def _save_state(self) -> None:
        data = {
            "user_folder_id": self.user_folder_id,
            "root_folder_id": self._root_id,
            "root_folder_link": folder_link(self._root_id) if self._root_id else None,
            "date_folders": self._date_folders,
        }
        tmp = self.state_file.with_suffix(".part")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    # ---------------- auth ----------------
    def _credentials(self, interactive: bool = False):
        from google.auth.exceptions import RefreshError, TransportError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = None
        if self.token_file.is_file():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_file))
            except ValueError:
                creds = None
        if creds and not creds.has_scopes(self.scopes):
            log.warning("token.json няма нужните права %s — нужна е нова авторизация", self.scopes)
            creds = None

        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self.token_file.write_text(creds.to_json(), encoding="utf-8")
                return creds
            except TransportError as e:
                raise UploadError(f"Няма връзка с Google ({e})") from e
            except RefreshError as e:
                if not interactive:
                    raise AuthRequiredError(
                        f"Google токенът е невалиден/изтекъл ({e}). Пуснете: python main.py --auth-gdrive"
                    ) from e
                creds = None

        if not interactive:
            raise AuthRequiredError("Липсва Google авторизация. Пуснете: python main.py --auth-gdrive")
        if not self.credentials_file.is_file():
            raise AuthRequiredError(f"Липсва {self.credentials_file} (OAuth Client ID от Google Cloud Console)")

        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_file), self.scopes)
        creds = flow.run_local_server(port=0, open_browser=True, prompt="consent",
                                      authorization_prompt_message="Отваря се браузър за вход в Google...",
                                      success_message="Готово! Можете да затворите този прозорец.")
        self.token_file.write_text(creds.to_json(), encoding="utf-8")
        log.info("Google Drive авторизацията е запазена в %s", self.token_file)
        return creds

    def _svc(self, interactive: bool = False):
        if self._service is None:
            import google_auth_httplib2
            import httplib2
            from googleapiclient.discovery import build

            creds = self._credentials(interactive=interactive)
            http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=120))
            self._service = build("drive", "v3", http=http, cache_discovery=False)
        return self._service

    def authorize_interactive(self) -> str:
        """Еднократна авторизация от конзолата (--auth-gdrive). Връща линк към папката."""
        self._service = None
        self._svc(interactive=True)
        root = self._ensure_root()
        return folder_link(root)

    # ---------------- folders ----------------
    def _ensure_root(self) -> str:
        svc = self._svc()
        if self.user_folder_id:
            try:
                meta = svc.files().get(fileId=self.user_folder_id, fields="id,name,trashed,mimeType",
                                       supportsAllDrives=True).execute()
            except Exception as e:  # noqa: BLE001
                raise UploadError(f"Папката {self.user_folder_id} не е достъпна: {e}") from e
            if meta.get("mimeType") != FOLDER_MIME or meta.get("trashed"):
                raise UploadError(f"GDRIVE_FOLDER ({self.user_folder_id}) не е активна папка")
            if self._root_id != self.user_folder_id:
                self._root_id = self.user_folder_id
                self._date_folders = {}
                self._save_state()
                log.info("Google Drive папка: '%s' -> %s", meta.get("name"), folder_link(self._root_id))
            return self._root_id

        if self._root_id:
            try:
                meta = svc.files().get(fileId=self._root_id, fields="id,trashed").execute()
                if not meta.get("trashed"):
                    return self._root_id
            except Exception:  # noqa: BLE001
                log.warning("Запазената Drive папка липсва — създавам нова")
        body = {"name": self.cfg.gdrive_root_folder_name, "mimeType": FOLDER_MIME}
        created = svc.files().create(body=body, fields="id").execute()
        self._root_id = created["id"]
        self._date_folders = {}
        self._save_state()
        (self.cfg.state_dir / "gdrive_folder_link.txt").write_text(folder_link(self._root_id), encoding="utf-8")
        log.info("Създадена Google Drive папка '%s': %s", self.cfg.gdrive_root_folder_name, folder_link(self._root_id))
        return self._root_id

    def _find_child(self, parent: str, name: str, folder: bool) -> str | None:
        q = f"name = '{name}' and '{parent}' in parents and trashed = false"
        if folder:
            q += f" and mimeType = '{FOLDER_MIME}'"
        res = self._svc().files().list(q=q, fields="files(id)", pageSize=1, spaces="drive",
                                       supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def _target_folder(self, date: str) -> str:
        root = self._ensure_root()
        if not self.cfg.gdrive_date_subfolders:
            return root
        fid = self._date_folders.get(date)
        if fid:
            return fid
        fid = self._find_child(root, date, folder=True)
        if not fid:
            body = {"name": date, "mimeType": FOLDER_MIME, "parents": [root]}
            fid = self._svc().files().create(body=body, fields="id", supportsAllDrives=True).execute()["id"]
        self._date_folders[date] = fid
        self._save_state()
        return fid

    # ---------------- upload ----------------
    def upload(self, path: Path, date: str) -> str:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        with self._lock:
            try:
                folder = self._target_folder(date)
                existing = self._find_child(folder, path.name, folder=False)
                if existing:
                    log.info("Вече е в Drive (пропускам): %s", path.name)
                    return existing
                media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=True, chunksize=CHUNK)
                req = self._svc().files().create(
                    body={"name": path.name, "parents": [folder]},
                    media_body=media,
                    fields="id",
                    supportsAllDrives=True,
                )
                resp = None
                while resp is None:
                    _, resp = req.next_chunk(num_retries=3)
                log.info("☁ Качено в Google Drive: %s (id=%s)", path.name, resp["id"])
                return resp["id"]
            except AuthRequiredError:
                self._service = None
                raise
            except HttpError as e:
                self._service = None
                status = getattr(e.resp, "status", 0)
                if status in (401, 403) and "storageQuotaExceeded" in str(e):
                    raise UploadError("Google Drive е пълен (storageQuotaExceeded)", retry_after=3600) from e
                if status == 404:
                    # папката е изтрита -> изчисти кеша и опитай пак следващия път
                    self._date_folders.clear()
                    if not self.user_folder_id:
                        self._root_id = None
                raise UploadError(f"Drive HTTP {status}: {e}") from e
            except UploadError:
                self._service = None
                raise
            except Exception as e:  # noqa: BLE001  (мрежови/SSL/timeout)
                self._service = None
                raise UploadError(f"Drive грешка: {type(e).__name__}: {e}") from e
