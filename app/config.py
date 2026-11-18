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

    # Claude API
    anthropic_api_key: str = ""
    model_strong: str = "claude-opus-5-5"   # writing and judgement
    model_cheap: str = "claude-haiku-5-5"   # classification, scoring, summaries
    daily_budget_usd: float = 3.0           # hard stop for LLM spend per day

    # Schedule (Europe/London). Inbox is checked at these times only; when new
    # PhD-related email is found, the run carries on and processes it fully.
    timezone: str = "Europe/London"
    inbox_check_times: list[str] = ["09:00", "14:00", "17:00", "20:00"]
    daily_run_time: str = "09:00"           # site sweep, outreach and reminder drafts, report
    findaphd_archive_dir: Path = ROOT / "data" / "findaphd"
    findaphd_page_wait_seconds: PositiveInt = 40
    failed_pages_dir: Path = ROOT / "data" / "samples" / "failed"

    # Telegram (phase 6)
    telegram_bot_token: str = ""
    telegram_owner_id: int = 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
