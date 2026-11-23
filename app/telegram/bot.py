"""Farbod's Telegram bot: a guided review of drafts, stage by stage, and a digest of everything else.

Stages, in order: replies to supervisors, reminders, new first emails (portals come in phase 8).
Each stage shows its drafts numbered ("یادآوری ۳ از ۱۴ · #27") with Approve / Edit / Deny, then asks
whether the stage is finished, checks the database and Gmail, and only then offers the next stage.
Nothing reaches a professor without Approve. Reports and tasks arrive grouped in one digest.
"""
import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.config import get_settings
from app.db.base import session_scope
from app.db.models import Draft, LLMCall, Meta, Notification, Position, UserTask
from app.knowledge.loader import build_context
from app.outreach.gmail_sync import mark_denied, record_sent, reminder_number, sync_drafts
from app.rules.text_checks import check_outgoing_text, fix_dash_connectors
from app.telegram.api import TelegramAPI
from app.telegram.fmt import bold, fa, quote

log = logging.getLogger("telegram")
OPEN = ("pending", "approved")
STAGES = [("reply", "جواب به ایمیل استادها", "جواب"), ("initial", "ایمیل‌های اولیه جدید", "ایمیل جدید"),
          ("reminder", "یادآوری‌ها", "یادآوری")]
PORTALS = "پورتال‌ها (فاز ۸، هنوز ساخته نشده)"
STAGE = {k: (title, one) for k, title, one in STAGES}
FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
COMMANDS = [("drafts", "بررسی draftها، مرحله به مرحله"), ("new", "شروع یه گفتگوی تازه با دستیار"), ("status", "وضعیت: پوزیشن‌ها، draftها، هزینه امروز"),
            ("tasks", "کارهایی که فقط خودت می‌تونی بکنی"), ("show", "نشون دادن یه draft با شماره‌ش"),
            ("cancel", "لغو ویرایش"), ("help", "راهنما")]
HELP = ("هر سؤالی داری یا هر کاری می‌خوای، همینجا بنویس. مثلاً «کدوم پوزیشن‌ها این هفته deadline دارن؟» یا "
        "«ایمیل استاد پوزیشن #120 رو پیدا کردم: x@uni.ac.uk، براش ایمیل بنویس».\n"
        "برای حرف زدن درباره یه draft، روی پیامش Reply بزن. ایمیل فقط با دکمه ✅ Approve فرستاده میشه.\n\n"
        "دستورها:\n/drafts بررسی draftها، مرحله به مرحله\n/new گفتگوی تازه\n/show 27 نشون دادن draft شماره ۲۷\n"
        "/status وضعیت\n/tasks کارهای تو\n/cancel لغو ویرایش")


def n_fa(n: int) -> str:
    return str(n).translate(FA_DIGITS)


class EditedEmail(BaseModel):
    subject: str = Field(description="Subject, unchanged unless the instruction asks to change it")
    body: str = Field(description="The full rewritten email")


EDIT_INSTRUCTIONS = """Rewrite this email following Farbod's instruction (it may be in Persian). Keep everything he did
not ask to change. English. Follow the SOPs. Never use a dash as a sentence connector. Use only verified profile
facts. Keep the greeting and the sign-off."""


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _rows(d: Draft):
    return [[("✅ Approve", f"a:{d.id}"), ("✏️ Edit", f"e:{d.id}"), ("❌ Deny", f"d:{d.id}")]]


def render(s, d: Draft, position_in_stage: str = "") -> tuple[str, str]:
    """-> (header in Persian, the email). Every line of the header carries a Persian label."""
    p = s.get(Position, d.position_id) if d.position_id else None
    title = STAGE.get(d.kind, (d.kind, d.kind))[1]
    if d.kind == "reminder" and reminder_number(d):
        title += f" (شماره {n_fa(reminder_number(d))})"
    head = [bold(f"✉️ {title} {position_in_stage} · draft #{d.id}".replace("  ", " "))]
    if d.version > 1:
        head.append(fa(f"نسخه {n_fa(d.version)} (ویرایش‌شده)"))
    if p:
        head.append(fa(f"پوزیشن: {p.title} ({p.university})"))
        if p.deadline_date:
            head.append(fa(f"مهلت: {p.deadline_date.isoformat()}"))
        if d.kind == "initial" and p.score:
            head.append(fa(f"score: {p.score}"))
        if d.kind == "reminder":
            last = max([x for x in (p.reminder_4, p.reminder_3, p.reminder_2, p.reminder_1, p.email_sent) if x] or ["نامعلوم"])
            head.append(fa(f"آخرین ایمیل یا یادآوری قبلی: {last}"))
    head.append(fa(f"گیرنده: {d.to}"))
    head.append(fa(f"موضوع: {d.subject}"))
    if d.attachments:
        head.append(fa("پیوست: " + ", ".join(Path(a).name for a in d.attachments)))
    notes = [n for n in (d.notes or []) if not str(n).startswith("reminder ")]
    if notes:
        head.append(bold("یادداشت‌ها:"))
        head += [fa(f"• {n}") for n in notes]
    head += [fa(f"⚠️ {i}") for i in (d.issues or [])]
    return "\n".join(head), quote(d.body)


class Bot:
    def __init__(self, api: TelegramAPI, owner_id: int, gmail_factory=None, llm_factory=None, sleep=time.sleep):
        self.api, self.owner = api, owner_id
        self._gmail_factory, self._llm_factory = gmail_factory, llm_factory
        self._gmail = None
        self._sleep = sleep

    # ------------------------------------------------------------------ helpers
    def gmail(self):
        if self._gmail is None:
            if self._gmail_factory is None:
                from app.gmail.client import GmailClient
                self._gmail_factory = GmailClient
            self._gmail = self._gmail_factory()
        return self._gmail

    def llm(self):
        if self._llm_factory is None:
            from app.llm.client import LLMClient
            self._llm_factory = LLMClient
        return self._llm_factory()

    def today(self) -> date:
        return datetime.now(ZoneInfo(get_settings().timezone)).date()

    def say(self, html_text: str, rows=None) -> int:
        """html_text is already formatted (fa(), bold(), quote())."""
        return self.api.send(self.owner, html_text, rows)

    def tell(self, text: str, rows=None) -> int:
        """Plain Persian text: shaped right to left and escaped."""
        return self.say(fa(text), rows)

    @staticmethod
    def pending(s, kind: str | None = None) -> list[Draft]:
        q = select(Draft).where(Draft.status == "pending").order_by(Draft.id)
        if kind:
            q = q.where(Draft.kind == kind)
        return list(s.scalars(q))

    def show_draft(self, s, d: Draft, position_in_stage: str = "", prefix: str = ""):
        head, body = render(s, d, position_in_stage)
        if prefix:
            head = prefix + "\n\n" + head
        if d.kind == "reply":                       # what the supervisor wrote, from the inbox analysis
            n = s.scalars(select(Notification).where(Notification.ref_type == "draft",
                                                     Notification.ref_id == d.id)).first()
            if n:
                head = fa(n.text) + "\n\n" + head
        text = head + "\n\n" + body
        if len(text) > 3900:                        # header and email as two messages
            self.say(head)
            if len(body) > 3900:                    # a quote block must not be cut in the middle
                body = quote(d.body[:3400] + "\n[...]") + "\n" + fa("متن کامل تو Gmail Drafts هست.")
            mid = self.say(body, _rows(d))
        else:
            mid = self.say(text, _rows(d))
        s.merge(Meta(key=f"tg:msg:{d.id}", value=mid))
        s.merge(Meta(key=f"tg:mid:{mid}", value=d.id))   # a Reply to this message edits this draft
        s.commit()

    def _close_buttons(self, s, draft_id: int):
        m = s.get(Meta, f"tg:msg:{draft_id}")
        if m:
            self.api.edit_buttons(self.owner, int(m.value))

    # ------------------------------------------------------------------ stages
    def next_stage(self, s, after: str | None = None) -> str | None:
        keys = [k for k, _, _ in STAGES]
        start = keys.index(after) + 1 if after in keys else 0
        for k in keys[start:]:
            if self.pending(s, k):
                return k
        return None

    def overview(self, s) -> str:
        parts = [f"{title}: {n_fa(len(self.pending(s, k)))}" for k, title, _ in STAGES if self.pending(s, k)]
        return "، ".join(parts)

    def offer_review(self, s):
        """Called at the end of a run: the four categories, then a start button."""
        stage = self.next_stage(s)
        if stage is None:
            self.tell("✅ draftی منتظر تصمیم تو نیست.")
            return
        lines = [bold("📬 ایمیل‌ها آماده‌ن. به این ترتیب با هم می‌بینیمشون:")]
        for i, (k, title, _) in enumerate(STAGES, 1):
            lines.append(fa(f"{n_fa(i)}. {title}: {n_fa(len(self.pending(s, k)))}"))
        lines.append(fa(f"{n_fa(len(STAGES) + 1)}. {PORTALS}"))
        lines.append(fa(f"از «{STAGE[stage][0]}» شروع کنیم؟"))
        self.say("\n".join(lines), [[("▶️ شروع", f"stage:{stage}"), ("بعداً", "later")]])

    def present_stage(self, s, stage: str):
        drafts = self.pending(s, stage)
        if not drafts:
            return self.finish_stage(s, stage)
        s.merge(Meta(key="tg:stage", value=stage))
        s.commit()
        title, one = STAGE[stage]
        self.say(bold(f"📌 {title}: {n_fa(len(drafts))} تا") + "\n" +
                 fa("هر کدوم رو Approve، Edit یا Deny کن. آخرش بگو تموم شد تا چک کنم."))
        total = len(drafts)
        for i, d in enumerate(drafts, 1):
            self.show_draft(s, d, f"{n_fa(i)} از {n_fa(total)}")
        rows = [[("✅ این مرحله تموم شد", f"done:{stage}")]]
        if total >= 2:
            rows.insert(0, [(f"✅ Approve همه {n_fa(total)} تا", f"all:{stage}")])
        self.say(bold(f"پایان {title}") + "\n" + fa("وقتی همه رو بررسی کردی:"), rows)

    def finish_stage(self, s, stage: str):
        """Check the database and Gmail; move on only when nothing is left in this stage."""
        try:
            sync_drafts(s, self.gmail(), self.today())   # sent or deleted in Gmail since
        except Exception as exc:
            log.warning("Gmail check failed: %s", exc)
        left = self.pending(s, stage)
        title = STAGE[stage][0]
        if left:
            lines = [bold(f"⏳ هنوز {n_fa(len(left))} مورد از {title} مونده:")]
            lines += [fa(f"• draft #{d.id} به {d.to}") for d in left]
            self.say("\n".join(lines), [[("🔁 دوباره نشونشون بده", f"stage:{stage}")]])
            return
        nxt = self.next_stage(s, stage)
        if nxt:
            self.say(bold(f"✅ {title} تموم شد.") + "\n" +
                     fa(f"تو Gmail هم چیزی جا نمونده. مرحله بعد: {STAGE[nxt][0]} ({n_fa(len(self.pending(s, nxt)))} تا). آماده‌ای؟"),
                     [[("▶️ بریم", f"stage:{nxt}"), ("بعداً", "later")]])
        else:
            s.merge(Meta(key="tg:stage", value=None))
            s.commit()
            self.say(bold(f"✅ {title} تموم شد. همه ایمیل‌ها بررسی شدن.") + "\n" +
                     fa(f"مرحله بعدی: {PORTALS}."))

    # ------------------------------------------------------------------ outbox
    def flush(self, s) -> int:
        """Messages go out in the order the steps happened, one per step. Drafts are never pushed on their
        own: a 'review' message (end of a run) opens the review. Single inbox items are not sent either;
        the run sends them together as its inbox summary."""
        if not s.get(Meta, "tg:baseline"):
            now = _now()
            for n in s.scalars(select(Notification).where(Notification.sent_at.is_(None))):
                n.sent_at = now
            s.add(Meta(key="tg:baseline", value=now.isoformat()))
            s.commit()
            self.say(bold("🤖 ربات PhD Assistant وصل شد.") + "\n\n" + fa(HELP))
            return 0
        sent = 0
        for n in list(s.scalars(select(Notification).where(Notification.sent_at.is_(None)).order_by(Notification.id))):
            n.sent_at = _now()
            s.commit()
            if n.ref_type == "draft" or (n.kind == "email" and n.ref_type != "pipeline"):
                continue                            # shown in the review / in the inbox summary
            if n.kind == "review" and n.text == "replies" and self.pending(s, "reply"):
                self.say(bold("📨 جواب‌هایی که برای ایمیل‌های این نوبت نوشتم:"))
                self.present_stage(s, "reply")
            elif n.kind == "review":
                self.offer_review(s)
            elif n.kind == "stage":
                self.say(bold(n.text))
            else:
                self.say("\n".join(fa(x) for x in n.text.split("\n")))
            sent += 1
        return sent

    # ------------------------------------------------------------------ actions
    def approve(self, s, d: Draft) -> str:
        if d.status not in OPEN:
            return f"draft #{d.id} قبلاً {d.status} شده."
        blocking = [i for i in (d.issues or []) if str(i).startswith("missing")]
        if blocking:
            return (f"draft #{d.id} فرستاده نشد: {blocking[0]}. "
                    "فایل رو اضافه کن و بعد Edit بزن، یا متن رو طوری عوض کن که ادعای پیوست نکنه.")
        missing = [a for a in (d.attachments or []) if not Path(a).exists()]
        if missing:
            return f"draft #{d.id} فرستاده نشد: پیوست پیدا نشد ({Path(missing[0]).name})."
        gmail = self.gmail()
        meta = s.get(Meta, f"gmail-draft:{d.id}")
        if meta and gmail.draft_exists(meta.value["draft"]):
            try:
                msg = gmail.send_draft(meta.value["draft"])
            except Exception:
                msg = gmail.sent_in_thread(d.thread_id, 0) if d.thread_id else None
                if not msg:
                    raise
        else:
            msg = gmail.send(d.to, d.subject, d.body, thread_id=d.thread_id, in_reply_to=d.in_reply_to,
                             attachments=[Path(a) for a in (d.attachments or [])])
        record_sent(s, d, msg, self.today())
        s.commit()
        self._close_buttons(s, d.id)
        return f"✅ draft #{d.id} فرستاده شد به {d.to}"

    def deny(self, s, d: Draft) -> str:
        if d.status not in OPEN:
            return f"draft #{d.id} قبلاً {d.status} شده."
        meta = s.get(Meta, f"gmail-draft:{d.id}")
        if meta:
            try:
                self.gmail().delete_draft(meta.value["draft"])
            except Exception as exc:
                log.warning("could not delete Gmail draft %s: %s", meta.value, exc)
        mark_denied(s, d, self.today())
        s.commit()
        self._close_buttons(s, d.id)
        return f"❌ draft #{d.id} رد شد. دوباره برای این مورد draft ساخته نمیشه."

    def apply_edit(self, s, d: Draft, text: str):
        if text.lstrip().lower().startswith(("dear ", "hi ", "hello ")):
            subject, body = d.subject, text.strip()
        else:
            out = self.llm().complete(
                "edit_email", f"Instruction from Farbod:\n{text}\n\nSubject: {d.subject}\n\nEmail:\n{d.body}",
                context=build_context("edit_email"), instructions=EDIT_INSTRUCTIONS, output=EditedEmail)
            subject, body = (out.subject or d.subject), out.body
        d.subject, d.body = fix_dash_connectors(subject), fix_dash_connectors(body)
        from app.outreach.initial import _claimed_but_missing
        d.issues = ([f"{i.kind}: {i.message} [{i.snippet}]" for i in check_outgoing_text(d.body, outreach=d.kind != "reply")]
                    + _claimed_but_missing(d.body, d.attachments or []))
        d.version += 1
        meta = s.get(Meta, f"gmail-draft:{d.id}")
        if meta:                                # keep Gmail Drafts in step with the edited text
            gmail = self.gmail()
            gmail.delete_draft(meta.value["draft"])
            ref = gmail.create_draft(d.to, d.subject, d.body, thread_id=d.thread_id if d.kind != "initial" else None,
                                     in_reply_to=d.in_reply_to,
                                     attachments=[Path(a) for a in (d.attachments or []) if Path(a).exists()])
            if d.kind == "initial":
                d.thread_id = ref["thread"]
            meta.value = ref
        editing = s.get(Meta, "tg:editing")
        if editing:
            s.delete(editing)
        s.commit()
        self._close_buttons(s, d.id)
        self.show_draft(s, d, prefix=bold("✏️ نسخه ویرایش‌شده:"))

    # ------------------------------------------------------------------ updates
    def _callback(self, s, data: str):
        action, _, ref = data.partition(":")
        if action == "later":
            self.tell("باشه. هر وقت خواستی /drafts رو بزن.")
            return
        if action == "no":
            self.tell("باشه، چیزی فرستاده نشد.")
            return
        if action == "stage":
            self.present_stage(s, ref)
            return
        if action == "done":
            self.finish_stage(s, ref)
            return
        if action == "all":
            n = len(self.pending(s, ref))
            self.say(fa(f"⚠️ {n_fa(n)} ایمیل از «{STAGE[ref][0]}» همین الان فرستاده میشه و قابل برگشت نیست. مطمئنی؟"),
                     [[(f"✅ بله، هر {n_fa(n)} تا رو بفرست", f"allyes:{ref}"), ("انصراف", "no")]])
            return
        if action == "allyes":
            results = []
            for d in self.pending(s, ref):
                try:
                    results.append(self.approve(s, d))
                except Exception as exc:
                    s.rollback()
                    results.append(f"⚠️ draft #{d.id}: {type(exc).__name__}")
            self.say("\n".join(fa(r) for r in results) or fa("draftی منتظر نبود."))
            self.finish_stage(s, ref)
            return
        d = s.get(Draft, int(ref)) if ref.isdigit() else None
        if d is None:
            self.tell("این draft پیدا نشد.")
            return
        if action == "a":
            self.tell(self.approve(s, d))
        elif action == "d":
            self.tell(self.deny(s, d))
        elif action == "e":
            if d.status not in OPEN:
                self.tell(f"draft #{d.id} قبلاً {d.status} شده.")
                return
            s.merge(Meta(key="tg:editing", value=d.id))
            s.commit()
            self.tell(f"✏️ ویرایش draft #{d.id}: متن کامل جدید ایمیل رو بفرست (با Dear شروع بشه)، "
                      "یا بگو چی رو عوض کنم، مثلاً «کوتاه‌ترش کن».\n/cancel برای لغو")

    def handle(self, s, update: dict):
        if "callback_query" in update:
            cq = update["callback_query"]
            if cq.get("from", {}).get("id") != self.owner:
                return
            self.api.answer(cq["id"])
            try:
                self._callback(s, cq.get("data", ""))
            except Exception as exc:
                s.rollback()
                log.exception("telegram action failed")
                self.tell(f"⚠️ کار انجام نشد: {type(exc).__name__}: {str(exc)[:200]}")
            return

        msg = update.get("message") or {}
        text = (msg.get("text") or "").strip()
        sender = msg.get("from", {}).get("id")
        if not text:
            return
        if not self.owner:
            if text.startswith("/start"):
                self.api.send(msg["chat"]["id"], fa(f"chat id شما: {sender}\nاین عدد رو تو .env بذار: ") +
                              f"<code>TELEGRAM_OWNER_ID={sender}</code>")
            return
        if sender != self.owner:
            return

        # Which draft is he talking about? A Reply to its message, "#27", or the one he pressed Edit on.
        target = None
        reply_to = (msg.get("reply_to_message") or {}).get("message_id")
        if reply_to:
            m = s.get(Meta, f"tg:mid:{reply_to}")
            target = s.get(Draft, int(m.value)) if m else None
        only_ref = re.fullmatch(r"#?(\d+)", text)
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            cmd = cmd.split("@")[0]
            if cmd == "/cancel":
                ed = s.get(Meta, "tg:editing")
                if ed:
                    s.delete(ed)
                    s.commit()
                self.tell("ویرایش لغو شد.")
            elif cmd == "/new":
                from app.chat.agent import reset
                reset(s)
                self.tell("گفتگوی تازه شروع شد.")
            elif cmd in ("/drafts", "/start"):
                stage = self.next_stage(s)
                if stage:
                    self.present_stage(s, stage)
                else:
                    self.tell("✅ draftی منتظر نیست.")
            elif cmd == "/show":
                ref = arg.strip().lstrip("#")
                if not ref.isdigit():
                    self.tell("شماره draft رو بفرست، مثلاً #27")
                    return
                d = s.get(Draft, int(ref))
                self.show_draft(s, d) if d else self.tell("این draft پیدا نشد.")
            elif cmd == "/status":
                self.say(self.status(s))
            elif cmd == "/tasks":
                tasks = list(s.scalars(select(UserTask).where(UserTask.done.is_(False)).order_by(UserTask.due_on)))
                lines = [bold(f"📝 کارهای تو ({n_fa(len(tasks))})")]
                lines += [fa(f"• {t.text}" + (f" (تا {t.due_on})" if t.due_on else "")) for t in tasks[:30]]
                self.say("\n".join(lines) if tasks else fa("کاری منتظر تو نیست."))
            else:
                self.tell(HELP)
            return
        if only_ref and not reply_to:
            d = s.get(Draft, int(only_ref.group(1)))
            self.show_draft(s, d) if d else self.tell("این draft پیدا نشد.")
            return
        editing = s.get(Meta, "tg:editing")
        if editing and editing.value and target is None:          # he pressed Edit: this text is the change
            d = s.get(Draft, int(editing.value))
            if d is None or d.status not in OPEN:
                s.delete(editing)
                s.commit()
                self.tell("اون draft دیگه منتظر نیست. ویرایش لغو شد.")
                return
            try:
                self.apply_edit(s, d, text)
            except Exception as exc:
                s.rollback()
                log.exception("edit failed")
                self.tell(f"⚠️ ویرایش انجام نشد: {type(exc).__name__}: {str(exc)[:200]}")
            return
        self.chat(s, text, about_draft=target.id if target else None)

    def chat(self, s, text: str, about_draft: int | None = None):
        """A free conversation with the assistant (Opus with tools); a Reply to a draft is about that draft."""
        from app.chat.agent import Agent
        from app.llm.client import BudgetExceeded
        self.api.typing(self.owner)
        agent = Agent(self.llm(), self, self.today())
        try:
            answer = agent.ask(s, text, about_draft)
        except BudgetExceeded:
            self.tell("⚠️ بودجه LLM امروز تموم شده. فردا دوباره بپرس، یا DAILY_BUDGET_USD رو تو .env بیشتر کن.")
            return
        except Exception as exc:
            s.rollback()
            log.exception("chat failed")
            self.tell(f"⚠️ جواب ندادم: {type(exc).__name__}: {str(exc)[:200]}")
            return
        self.tell(answer)
        for draft_id in dict.fromkeys(agent.shown):
            d = s.get(Draft, draft_id)
            if d:
                self.show_draft(s, d)

    def status(self, s) -> str:
        start = datetime.combine(self.today(), datetime.min.time())
        sent = s.scalar(select(func.count(Draft.id)).where(Draft.status == "sent"))
        open_pos = s.scalar(select(func.count(Position.id)).where(Position.closed.is_(False)))
        spent = s.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0.0)).where(LLMCall.created_at >= start))
        tasks = s.scalar(select(func.count(UserTask.id)).where(UserTask.done.is_(False)))
        lines = [bold("📊 وضعیت"), fa(f"پوزیشن‌های باز: {n_fa(open_pos)}"),
                 fa(f"draftهای منتظر: {self.overview(s) or 'هیچ'}"), fa(f"ایمیل‌های فرستاده‌شده: {n_fa(sent)}"),
                 fa(f"کارهای تو: {n_fa(tasks)}"),
                 fa(f"هزینه LLM امروز: ${spent:.2f} از ${get_settings().daily_budget_usd:.2f}")]
        return "\n".join(lines)

    # ------------------------------------------------------------------ loop
    def poll_once(self, timeout: int = 30):
        with session_scope() as s:
            m = s.get(Meta, "tg:offset")
            offset = int(m.value) if m else None
        updates = self.api.get_updates(offset, timeout=timeout)
        with session_scope() as s:
            for u in updates:
                s.merge(Meta(key="tg:offset", value=u["update_id"] + 1))
                s.commit()
                self.handle(s, u)
            if self.owner:
                self.flush(s)

    def setup_menu(self):
        try:
            self.api.set_commands(COMMANDS)
        except Exception as exc:                # the menu is a convenience; never stop the bot for it
            log.warning("could not set the command menu: %s", exc)

    def run_forever(self):
        self.setup_menu()
        while True:
            try:
                self.poll_once()
            except Exception:
                log.exception("telegram loop error")
                self._sleep(10)


def make_bot() -> Bot | None:
    st = get_settings()
    if not st.telegram_bot_token:
        return None
    return Bot(TelegramAPI(st.telegram_bot_token), st.telegram_owner_id)
