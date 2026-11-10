"""Thin wrapper over the Gmail API.

Auth: a Desktop OAuth client JSON in secrets/gmail_client.json; `phd gmail-auth`
opens the browser once and stores secrets/gmail_token.json (refreshed automatically).
"""
import base64
import json
import time
from email.message import EmailMessage as MimeMessage
from pathlib import Path

from app.config import get_settings

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]   # read, labels, drafts, send


class GmailAuthError(RuntimeError):
    pass


def _paths():
    d = get_settings().secrets_dir
    return d / "gmail_client.json", d / "gmail_token.json"


def authorize() -> str:
    """Interactive one-off login on Farbod's laptop. Returns the account address."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    client, token = _paths()
    if not client.exists():
        raise GmailAuthError(f"OAuth client file missing: {client}")
    flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json())
    return GmailClient().me


def build_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    _, token = _paths()
    if not token.exists():
        raise GmailAuthError("Gmail is not authorised yet: run `phd gmail-auth`")
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json())
    if not creds.valid:
        raise GmailAuthError("Gmail token is invalid: run `phd gmail-auth` again")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


RETRY_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError", "internalError"}


def _retryable(exc) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status in (429, 500, 502, 503, 504):
        return True
    if status == 403:
        try:
            errors = json.loads(exc.content.decode()).get("error", {}).get("errors", [])
            return any(e.get("reason") in RETRY_REASONS for e in errors)
        except Exception:
            return b"rateLimitExceeded" in (exc.content or b"")
    return False


class GmailClient:
    MIN_INTERVAL = 0.06          # ~16 calls/second, well under Gmail's per-user quota
    MAX_RETRIES = 7

    def __init__(self, service=None, sleep=time.sleep):
        self._svc = service or build_service()
        self._sleep = sleep
        self._last = 0.0
        self.me = self._exec(self._svc.users().getProfile(userId="me"))["emailAddress"].lower()

    def _exec(self, request):
        """Execute with throttling and exponential backoff on quota/server errors."""
        from googleapiclient.errors import HttpError
        delay = 2.0
        for attempt in range(self.MAX_RETRIES + 1):
            wait = self.MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                return request.execute()
            except HttpError as exc:
                if attempt == self.MAX_RETRIES or not _retryable(exc):
                    raise
                self._sleep(delay)
                delay = min(delay * 2, 64)

    def search(self, query: str, limit: int = 2000) -> list[str]:
        ids, token = [], None
        while len(ids) < limit:
            resp = self._exec(self._svc.users().messages().list(userId="me", q=query, pageToken=token,
                                                                maxResults=min(500, limit - len(ids))))
            ids += [m["id"] for m in resp.get("messages", [])]
            token = resp.get("nextPageToken")
            if not token:
                break
        return ids

    def get(self, msg_id: str) -> dict:
        return self._exec(self._svc.users().messages().get(userId="me", id=msg_id, format="full"))

    def send(self, to: str, subject: str, body: str, *, thread_id: str | None = None,
             in_reply_to: str | None = None, attachments: list[Path] = ()) -> str:
        """Used only after Farbod approves in Telegram (phase 6)."""
        msg = MimeMessage()
        msg["To"], msg["From"], msg["Subject"] = to, self.me, subject
        if in_reply_to:
            msg["In-Reply-To"] = msg["References"] = in_reply_to
        msg.set_content(body)
        for path in attachments:
            path = Path(path)
            maintype, subtype = ("application", "pdf") if path.suffix.lower() == ".pdf" else ("application", "octet-stream")
            msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        payload = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        return self._exec(self._svc.users().messages().send(userId="me", body=payload))["id"]
