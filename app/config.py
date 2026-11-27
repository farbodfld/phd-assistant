"""Application settings, read from environment variables or a .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", env_ignore_empty=True)

    # Database: SQLite for local dev and tests, PostgreSQL in production.
    database_url: str = f"sqlite:///{ROOT / 'data' / 'phd.db'}"

    # OAuth client and tokens (gitignored)
    secrets_dir: Path = ROOT / "secrets"

    # Where position folders (CV, statements, proposals...) live.
    positions_dir: Path = ROOT / "data" / "positions"
    # Master documents: the one CV (profile.yaml documents.master_cv), transcripts and certificates.
    documents_dir: Path = ROOT / "data" / "documents"

    # Phase 5: every email is a draft. Until the Telegram bot (phase 6) exists, drafts are also
    # created in Gmail Drafts so Farbod can read, edit and send them himself.
    gmail_drafts: bool = True
    outreach_daily_limit: int = 0          # 0 = no limit (the daily LLM budget still applies)

    # Claude API
    anthropic_api_key: str = ""
    model_strong: str = "claude-opus-5-5"   # writing and judgement
    model_cheap: str = "claude-haiku-5-5"   # classification, scoring, summaries
    daily_budget_usd: float = 3.0           # hard stop for LLM spend per day
    min_score: int = 35                     # discovered positions scoring below this are not tracked

    # Schedule (Europe/London). Inbox is checked at these times only (08:00 to 20:00); when new
    # PhD-related email is found, the run carries on and processes it fully. Discovery runs once a day
    # in its own thread, so a long site sweep never delays an inbox check.
    timezone: str = "Europe/London"
    inbox_check_times: list[str] = ["08:00", "14:00", "17:00", "20:00"]
    inbox_lookback_days: int = 5            # every inbox check re-reads at least this many days back
    portal_horizon_days: int = 21           # portal positions offered in Telegram: deadline within this many days
    portal_min_score: int = 60              # ...and at least this score (invited positions always count)
    portal_auto_days: int = 10              # the morning run prepares packs by itself when the deadline is this close
    portal_auto_min_score: int = 70         # ...and the score is at least this
    portal_daily_packs: int = 10            # most packs prepared by the morning run (no model calls; facts only)
    # Portal packs also go to Google Drive (for the Portal session when the agent runs on a server).
    # Turning this on needs `phd gmail-auth` once more, to add the drive.file scope.
    drive_packs: bool = False
    drive_packs_folder: str = "PhD Portal Packs"
    max_reminders: int = 4                  # reminders per position; after the last one, stop
    daily_run_time: str = "08:00"           # morning run: inbox, then both sites, then outreach and review
    findaphd_archive_dir: Path = ROOT / "data" / "findaphd"
    findaphd_page_wait_seconds: PositiveInt = 40
    failed_pages_dir: Path = ROOT / "data" / "samples" / "failed"

    # Telegram (phase 6)
    telegram_bot_token: str = ""
    telegram_owner_id: int = 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
