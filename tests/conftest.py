import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh SQLite database per test."""
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.config import get_settings
    get_settings.cache_clear()
    from app.db.base import init_db
    init_db(url)
    yield url
    get_settings.cache_clear()
