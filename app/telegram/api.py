"""A small Telegram Bot API client (long polling), no extra dependency."""
import time

import httpx

LIMIT = 4000          # Telegram allows 4096 characters per message


class TelegramError(RuntimeError):
    pass


def buttons(rows: list[list[tuple[str, str]]] | None) -> dict | None:
    """[[("✅ Approve", "a:12"), ...]] -> inline keyboard."""
    if rows is None:
        return None
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def chunks(text: str, size: int = LIMIT) -> list[str]:
    out, rest = [], text
    while len(rest) > size:
        cut = rest.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        out.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    return out + [rest]


class TelegramAPI:
    def __init__(self, token: str, client: httpx.Client | None = None, sleep=time.sleep):
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
        self._base = f"https://api.telegram.org/bot{token}/"
        self._http = client or httpx.Client(timeout=60)
        self._sleep = sleep

    def _call(self, method: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(4):
            r = self._http.post(self._base + method, json=params)
            data = r.json()
            if data.get("ok"):
                return data["result"]
            retry = (data.get("parameters") or {}).get("retry_after")
            if r.status_code == 429 and retry and attempt < 3:
                self._sleep(int(retry) + 1)
                continue
            raise TelegramError(f"{method}: {data.get('description', r.status_code)}")

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict]:
        return self._call("getUpdates", offset=offset, timeout=timeout,
                          allowed_updates=["message", "callback_query"])

    def send(self, chat_id: int, text: str, rows=None) -> int:
        """Telegram HTML. Long text is split at line breaks (tags never span lines); the buttons go on
        the last part. No artificial delay: a 429 from Telegram is waited out in _call.
        -> message_id of the last part."""
        parts = chunks(text)
        mid = 0
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            mid = self._call("sendMessage", chat_id=chat_id, text=part, parse_mode="HTML",
                             disable_web_page_preview=True,
                             reply_markup=buttons(rows) if last else None)["message_id"]
        return mid

    def set_commands(self, commands: list[tuple[str, str]]):
        """The bot's Menu button: every command with a short description, ready to tap."""
        self._call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])
        self._call("setChatMenuButton", menu_button={"type": "commands"})

    def typing(self, chat_id: int):
        try:
            self._call("sendChatAction", chat_id=chat_id, action="typing")
        except TelegramError:
            pass

    def edit_buttons(self, chat_id: int, message_id: int, rows=None):
        try:
            self._call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                       reply_markup=buttons(rows) or {"inline_keyboard": []})
        except TelegramError:
            pass                                # message too old or unchanged: not important

    def answer(self, callback_id: str, text: str = ""):
        try:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])
        except TelegramError:
            pass

    def close(self):
        self._http.close()
