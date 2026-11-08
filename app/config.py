"""Application settings, read from environment variables or a .env file."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # Database: SQLite for local dev and tests, PostgreSQL in production.
    database_url: str = f"sqlite:///{ROOT / 'data' / 'phd.db'}"

    # Where position folders (CV, statements, proposals...) live.
    positions_dir: Path = ROOT / "data" / "positions"

    # Claude API
    anthropic_api_key: str = ""
    model_strong: str = "claude-opus-5-5"   # writing and judgement
    model_cheap: str = "claude-haiku-5-5"   # classification, scoring, summaries
    daily_budget_usd: float = 3.0           # hard stop for LLM spend per day

    # Telegram (phase 6)
    telegram_bot_token: str = ""
    telegram_owner_id: int = 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
