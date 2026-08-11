import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.config import get_settings
from app.core.database import engine, session_scope
from app.routers import (
    case_studies,
    dashboard,
    directory,
    insights,
    media,
    settings as settings_router,
    users,
)

settings = get_settings()
logger = logging.getLogger("blujeansz")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail loudly at boot rather than on the first request if the database is
    # unreachable or pointed somewhere unexpected.
    try:
        async with session_scope() as session:
            await session.execute(text("select 1"))
        logger.info("Database connection established.")
    except Exception:
        logger.exception("Could not reach the database at startup.")
        raise

    yield
    await engine.dispose()


app = FastAPI(
    title="BLUJEANSZ CMS API",
    version="0.1.0",
    description=(
        "Content API for the BLUJEANSZ website. Supabase Auth issues the "
        "tokens; this service verifies them and owns all database access."
    ),
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """Turns constraint violations into an actionable message.

    A duplicate slug is the common case and is the editor's problem to fix, so
    it should read as a 409 rather than a 500.
    """
    message = str(getattr(exc, "orig", exc))

    if "slug" in message and "unique" in message.lower():
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "That slug is already in use. Choose a different one."},
        )

    logger.warning("Integrity error on %s: %s", request.url.path, message)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "That change conflicts with existing data."},
    )


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    # Never leak connection strings or SQL to the client.
    logger.exception("Database error on %s", request.url.path)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "The content service is temporarily unavailable."},
    )


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe for Railway. Does not touch the database."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/health/ready", tags=["meta"])
async def readiness() -> dict[str, str]:
    async with session_scope() as session:
        await session.execute(text("select 1"))
    return {"status": "ready"}


for router in (
    insights.router,
    case_studies.router,
    directory.router,
    media.router,
    users.router,
    settings_router.router,
):
    app.include_router(router, prefix=settings.api_prefix)

app.include_router(dashboard.router, prefix=settings.api_prefix)

# Development-only login, for running against a bare local Postgres with no
# Supabase Auth. Settings refuse to construct when this is combined with
# ENVIRONMENT=production, so it cannot be mounted in a deployed environment.
if settings.local_auth_enabled:
    from app.routers import local_auth

    logger.warning(
        "LOCAL_AUTH_ENABLED is on: /auth/local/login is mounted and self-signed "
        "tokens are accepted. Development only."
    )
    app.include_router(local_auth.router, prefix=settings.api_prefix)
