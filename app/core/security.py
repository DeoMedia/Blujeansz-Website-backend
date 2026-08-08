"""Authentication: verifying Supabase Auth tokens and resolving CMS identity.

Supabase Auth remains the identity provider — it issues the tokens, handles
password hashing, resets and invites. This service does not mint its own
tokens; it verifies Supabase's and maps them onto a `profiles` row.

Tokens are RS256-signed. The public keys come from the project's JWKS endpoint
and are cached, so verification is a local operation with no network call on
the request path.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.models import Profile

settings = get_settings()
bearer_scheme = HTTPBearer(auto_error=False)

# Role hierarchy, mirroring public.role_rank() in the SQL migrations.
ROLE_RANK: dict[str, int] = {
    "super_admin": 4,
    "admin": 3,
    "editor": 2,
    "author": 1,
}


class _JWKSCache:
    """Caches the project's signing keys, refreshing on rotation.

    A token signed with an unseen `kid` triggers exactly one refresh; without
    that, key rotation would reject every request until a redeploy.
    """

    def __init__(self, ttl: timedelta = timedelta(hours=1)) -> None:
        self._keys: dict[str, Any] = {}
        self._fetched_at: datetime | None = None
        self._ttl = ttl

    def _is_stale(self) -> bool:
        if self._fetched_at is None:
            return True
        return datetime.now(timezone.utc) - self._fetched_at > self._ttl

    async def _refresh(self) -> None:
        url = f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers={"apikey": settings.supabase_anon_key})
            response.raise_for_status()
            payload = response.json()

        self._keys = {
            key["kid"]: jwt.PyJWK(key).key
            for key in payload.get("keys", [])
            if "kid" in key
        }
        self._fetched_at = datetime.now(timezone.utc)

    async def get(self, kid: str) -> Any:
        if self._is_stale() or kid not in self._keys:
            await self._refresh()

        key = self._keys.get(kid)
        if key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token signing key is not recognised.",
            )
        return key


jwks_cache = _JWKSCache()


async def decode_token(token: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token."
        ) from exc

    kid = header.get("kid")
    if not kid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token is missing a key id."
        )

    key = await jwks_cache.get(kid)

    try:
        return jwt.decode(
            token,
            key=key,
            algorithms=["RS256", "ES256"],
            audience=settings.supabase_jwt_audience,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session has expired."
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token."
        ) from exc


async def get_current_profile(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> Profile:
    """Resolves the caller to an active CMS profile, or refuses the request."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = await decode_token(credentials.credentials)
    user_id = claims.get("sub")

    profile = (
        await session.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()

    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has no CMS profile.",
        )

    # An invited-but-unactivated or suspended account holds a valid token but
    # must not be able to act.
    if profile.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This account is {profile.status}.",
        )

    return profile


async def get_optional_profile(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> Profile | None:
    """Identity for endpoints that serve both public and authenticated callers.

    Used by preview: an anonymous visitor gets published content only, while a
    signed-in editor may also see drafts.
    """
    if credentials is None:
        return None
    try:
        return await get_current_profile(credentials, session)
    except HTTPException:
        return None


def require_role(minimum: str):
    """Dependency factory enforcing the role hierarchy."""

    async def dependency(profile: Profile = Depends(get_current_profile)) -> Profile:
        if ROLE_RANK.get(profile.role, 0) < ROLE_RANK[minimum]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the {minimum} role.",
            )
        return profile

    return dependency


def can_publish(profile: Profile) -> bool:
    """Mirrors public.auth_can_publish() in SQL."""
    return profile.role in {"super_admin", "admin"} or bool(profile.can_publish)


def require_publish_rights(profile: Profile) -> None:
    if not can_publish(profile):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to publish content.",
        )
