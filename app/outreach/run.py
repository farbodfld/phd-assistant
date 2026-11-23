"""The daily outreach step: sync sent drafts, draft due reminders, draft first emails, push to Gmail."""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Notification
from app.llm.client import BudgetExceeded, LLMRefused
from app.outreach.gmail_sync import push_drafts, sync_drafts
from app.outreach.initial import draft_initial, needs_initial
from app.outreach.reminder import draft_due_reminders


@dataclass
class OutreachReport:
    sent: int = 0
    deleted: int = 0
    reminders: int = 0
    initial: int = 0
    no_email: int = 0
    skipped: int = 0
    refused: int = 0
    waiting: int = 0            # positions left for the next run (budget or daily limit)
    pushed: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"ایمیل‌ها | {self.initial} ایمیل اولیه و {self.reminders} یادآوری draft شد"]
        if self.pushed:
            lines.append(f"{self.pushed} draft تو Gmail Drafts ساخته شد. بخون، اگه لازم بود ویرایش کن و Send بزن.")
        if self.sent:
            lines.append(f"{self.sent} ایمیل که فرستادی تو tracker ثبت شد")
        if self.deleted:
            lines.append(f"{self.deleted} draft که تو Gmail پاک کردی، رد شده حساب شد")
        if self.no_email:
            lines.append(f"{self.no_email} پوزیشن آدرس ایمیل supervisor نداشت (تو کارهای تو اضافه شد)")
        if self.skipped:
            lines.append(f"{self.skipped} پوزیشن طبق آگهی یا جواب قبلی استاد نباید ایمیل بگیره")
        if self.refused:
            lines.append(f"{self.refused} پوزیشن رو مدل ایمیل براش ننوشت (refusal)؛ تو کارهای تو اضافه شد")
        if self.waiting:
            lines.append(f"{self.waiting} پوزیشن دیگه برای اجرای بعدی موند")
        return "\n".join(lines + [f"⚠️ {e}" for e in self.errors[:5]])


def run_outreach(s: Session, llm, today: date, gmail=None, fetcher=None, limit: int | None = None) -> OutreachReport:
    r = OutreachReport()
    use_gmail = gmail is not None and get_settings().gmail_drafts
    if use_gmail:
        try:
            done = sync_drafts(s, gmail, today)
            r.sent, r.deleted = len(done["sent"]), len(done["deleted"])
        except Exception as exc:
            r.errors.append(f"Gmail sync: {type(exc).__name__}: {exc}")
    try:
        r.reminders = len(draft_due_reminders(s, llm, today))
    except BudgetExceeded:
        r.errors.append("daily model budget reached; reminders resume next run")
    except Exception as exc:
        r.errors.append(f"reminders: {type(exc).__name__}: {exc}")

    limit = limit if limit is not None else (get_settings().outreach_daily_limit or None)
    queue = needs_initial(s, today)
    for i, p in enumerate(queue):
        if limit is not None and r.initial >= limit:
            r.waiting = len(queue) - i
            break
        try:
            draft = draft_initial(s, p, llm, today, fetcher)
        except LLMRefused:
            # The model declines this advert (e.g. some biology topics). Retrying gives the same answer:
            # record it once and leave the email to Farbod.
            s.rollback()
            from app.db.models import Meta, UserTask
            s.merge(Meta(key=f"outreach:{p.id}", value="model declined; write it yourself"))
            s.add(UserTask(position_id=p.id, due_on=p.deadline_date,
                           text=f"مدل برای «{p.title}» ({p.university}) ایمیل ننوشت (refusal). اگه می‌خوای، خودت بنویس."))
            r.refused += 1
            s.commit()
            continue
        except BudgetExceeded:
            s.rollback()
            r.waiting = len(queue) - i
            r.errors.append("daily model budget reached; first emails resume next run")
            break
        except Exception as exc:
            s.rollback()
            r.errors.append(f"first email for #{p.id}: {type(exc).__name__}: {str(exc)[:150]}")
            continue
        if draft:
            r.initial += 1
        else:
            from app.db.models import Meta
            m = s.get(Meta, f"outreach:{p.id}")
            if m and str(m.value).startswith("skip"):
                r.skipped += 1
            else:
                r.no_email += 1
        s.commit()

    if use_gmail:
        try:
            r.pushed = push_drafts(s, gmail)
        except Exception as exc:
            r.errors.append(f"Gmail drafts: {type(exc).__name__}: {exc}")
    s.add(Notification(kind="report", ref_type="outreach", text=r.summary()))
    s.commit()
    return r
