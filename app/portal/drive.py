"""Upload portal packs to Google Drive so the Portal session can read them when the agent runs on a server.

Uses the same OAuth token as Gmail with the extra scope drive.file: the agent sees only the files it created.
Layout: My Drive/<drive_packs_folder>/<id> - <title> (<university>)/{portal_pack.json, Farbod_Fooladi_Portal_Pack.md}
Enable with DRIVE_PACKS=true, then run `phd gmail-auth` once so the token carries the Drive scope.
"""
import re
from pathlib import Path

from app.config import get_settings

FOLDER = "application/vnd.google-apps.folder"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"


class DriveAuthError(RuntimeError):
    pass


def build_drive():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    token = get_settings().secrets_dir / "gmail_token.json"
    if not token.exists():
        raise DriveAuthError("No Google token: run `phd gmail-auth`")
    creds = Credentials.from_authorized_user_file(str(token))
    if DRIVE_SCOPE not in (creds.scopes or []):
        raise DriveAuthError("The Google token has no Drive access: set DRIVE_PACKS=true and run `phd gmail-auth` again")
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def folder_name(pid: int, title: str, university: str | None) -> str:
    name = f"{pid} - {title}"[:90].rstrip()
    uni = (university or "").replace("The University of ", "").replace("University of ", "")
    return re.sub(r"[\\/]", " ", f"{name} ({uni[:40]})" if uni else name)


class DrivePacks:
    def __init__(self, service=None):
        self.svc = service or build_drive()

    def _find(self, name: str, parent: str | None, mime: str | None = None) -> str | None:
        q = [f"name = '{name.replace(chr(39), chr(92) + chr(39))}'", "trashed = false"]
        if parent:
            q.append(f"'{parent}' in parents")
        if mime:
            q.append(f"mimeType = '{mime}'")
        res = self.svc.files().list(q=" and ".join(q), spaces="drive", fields="files(id)").execute()
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def _folder(self, name: str, parent: str | None) -> str:
        fid = self._find(name, parent, FOLDER)
        if fid:
            return fid
        body = {"name": name, "mimeType": FOLDER, **({"parents": [parent]} if parent else {})}
        return self.svc.files().create(body=body, fields="id").execute()["id"]

    def _put(self, path: Path, parent: str, mime: str) -> str:
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
        fid = self._find(path.name, parent)
        if fid:
            return self.svc.files().update(fileId=fid, media_body=media, fields="id").execute()["id"]
        return self.svc.files().create(body={"name": path.name, "parents": [parent]}, media_body=media,
                                       fields="id").execute()["id"]

    def upload(self, pid: int, title: str, university: str | None, files: list[Path]) -> str:
        """Create or update the position's folder and its pack files. Returns the folder id."""
        root = self._folder(get_settings().drive_packs_folder, None)
        sub = self._folder(folder_name(pid, title, university), root)
        for f in files:
            mime = "application/json" if f.suffix == ".json" else "text/markdown"
            self._put(f, sub, mime)
        return sub
