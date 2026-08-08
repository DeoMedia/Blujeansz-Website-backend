from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment.

    Railway injects these as environment variables; locally they come from
    `.env`. Nothing here has a usable default that would let the app start
    against the wrong database by accident.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development")

    # --- Database -----------------------------------------------------------
    # Supabase Postgres. Use the *session pooler* URI (port 5432) rather than
    # the transaction pooler: asyncpg's prepared statements are incompatible
    # with pgbouncer in transaction mode.
    database_url: str

    # --- Supabase -----------------------------------------------------------
    supabase_url: str
    supabase_anon_key: str
    # Verifies the RS256 tokens Supabase Auth issues. Fetched from
    # {supabase_url}/auth/v1/.well-known/jwks.json at startup.
    supabase_jwt_audience: str = "authenticated"

    # Server-side only. Never expose this to the browser: it bypasses RLS.
    supabase_service_role_key: str | None = None

    # --- API ----------------------------------------------------------------
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:5173"

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        """SQLAlchemy needs the asyncpg driver in the URL scheme.

        Supabase hands out `postgresql://...`, which SQLAlchemy maps to the
        synchronous psycopg driver and then fails at runtime inside the async
        engine. Rewriting here means the copied-and-pasted URI just works.
        """
        if value.startswith("postgresql+asyncpg://"):
            return value
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        raise ValueError("database_url must be a postgres connection URI")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
