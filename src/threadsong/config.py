from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", hide_input_in_errors=True, populate_by_name=True
    )

    app_mode: Literal["demo", "setup", "live"] = "demo"
    public_base_url: str = "http://localhost:8080"
    database_url: SecretStr = Field(
        default=SecretStr("sqlite+aiosqlite:///.data/threadsong.db"),
        validation_alias=AliasChoices("DATABASE_URL", "SUPABASE_DB_URL"),
    )
    local_data_dir: Path = Path(".data")
    run_worker: bool = True
    port: int = Field(default=8080, ge=1, le=65535)
    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")
    supabase_storage_bucket: str = "songs"
    ando_api_key: SecretStr = SecretStr("")
    ando_workspace_id: str = ""
    ando_agent_member_id: str = ""
    ando_webhook_signing_secret: SecretStr = SecretStr("")
    ando_mention_token: str = "@Songbot"
    ando_allowed_conversations: str = ""
    gemini_api_key: SecretStr = SecretStr("")
    gemini_text_model: str = "gemini-3.8-flash"
    lyria_model: str = "lyria-3.5"
    generation_timeout_seconds: int = Field(default=300, ge=10, le=900)
    song_duration_seconds: int = Field(default=30, ge=10, le=180)
    worker_poll_seconds: float = Field(default=2, ge=0.1)
    job_lease_seconds: int = Field(default=420, ge=30)
    max_job_attempts: int = Field(default=4, ge=1)
    max_thread_messages: int = Field(default=150, ge=1, le=1000)
    max_context_chars: int = Field(default=30000, ge=1000, le=100000)

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value):
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(raw, str) and raw.startswith(("postgres://", "postgresql://")):
            url = make_url(raw).set(drivername="postgresql+asyncpg")
            query = dict(url.query)
            if "sslmode" in query:
                query["ssl"] = query.pop("sslmode")
            return url.set(query=query).render_as_string(hide_password=False)
        return value

    @property
    def allowed_conversations(self) -> set[str]:
        return {x.strip() for x in self.ando_allowed_conversations.split(",") if x.strip()}

    @model_validator(mode="after")
    def validate_live(self):
        if self.job_lease_seconds < self.generation_timeout_seconds + 90:
            raise ValueError(
                "JOB_LEASE_SECONDS must exceed GENERATION_TIMEOUT_SECONDS by 90 seconds"
            )
        if not self.ando_mention_token.strip():
            raise ValueError("ANDO_MENTION_TOKEN must not be empty")
        if self.app_mode == "live":
            required = [
                "supabase_url",
                "supabase_service_role_key",
                "ando_api_key",
                "ando_workspace_id",
                "ando_agent_member_id",
                "ando_webhook_signing_secret",
                "gemini_api_key",
            ]
            missing = []
            for name in required:
                value = getattr(self, name)
                if isinstance(value, SecretStr):
                    value = value.get_secret_value()
                if not value:
                    missing.append(name.upper())
            if missing:
                raise ValueError("Missing live configuration: " + ", ".join(missing))
            if not self.public_base_url.startswith("https://"):
                raise ValueError(
                    "PUBLIC_BASE_URL must be HTTPS in live mode (a tunnel works locally)"
                )
            if not self.database_url.get_secret_value().startswith("postgresql+asyncpg://"):
                raise ValueError("Live mode requires a postgresql+asyncpg:// DATABASE_URL")
        return self
