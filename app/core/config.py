from functools import lru_cache

from pydantic import Field, field_validator, model_validator
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

    # --- Local development auth ---------------------------------------------
    # Supabase Auth is a hosted service, so there is no identity provider when
    # working against a plain local Postgres. Enabling this adds a second login
    # path that mints its own tokens — strictly for local development.
    #
    # It is refused outright when ENVIRONMENT is production (see the validator
    # below), so it cannot be switched on in a deployed environment even by
    # setting the variable.
    local_auth_enabled: bool = False
    local_auth_secret: str = "local-development-only-not-a-production-secret"

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

    @model_validator(mode="after")
    def _refuse_local_auth_in_production(self) -> "Settings":
        """Hard stop rather than a warning.

        A deployment that somehow carried LOCAL_AUTH_ENABLED=true would accept
        self-minted tokens and bypass Supabase entirely. Refusing to start is
        the only safe response — a running-but-insecure API is worse than one
        that fails loudly on boot.
        """
        if self.local_auth_enabled and self.is_production:
            raise ValueError(
                "LOCAL_AUTH_ENABLED cannot be used with ENVIRONMENT=production. "
                "It exists only for local development against a bare Postgres."
            )
        return self

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
