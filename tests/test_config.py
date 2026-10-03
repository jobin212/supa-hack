from threadsong.config import Settings


def test_compute_builtin_database_url_is_supported(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "SUPABASE_DB_URL", "postgresql://app:example@database:5432/postgres?sslmode=require"
    )
    settings = Settings(_env_file=None)
    assert settings.database_url.get_secret_value() == (
        "postgresql+asyncpg://app:example@database:5432/postgres?ssl=require"
    )


def test_explicit_database_url_overrides_compute_builtin(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://other:example@database/postgres")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///demo.db")
    assert Settings(_env_file=None).database_url.get_secret_value() == "sqlite+aiosqlite:///demo.db"
