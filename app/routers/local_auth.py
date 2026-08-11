"""Local development login.

Supabase Auth is a hosted service, so there is no identity provider when the
API runs against a plain local Postgres. This issues a token the API's own
verifier accepts, so the admin UI can be exercised end to end offline.

This router is only mounted when LOCAL_AUTH_ENABLED is true, and settings
refuse to construct at all if that is combined with ENVIRONMENT=production. It
is never reachable in a deployed environment.

Passwords live in `dev_credentials`, created by scripts/setup_local_dev.py.
That table is deliberately NOT in supabase/migrations — it must never exist in
a real database. Hashing is scrypt from the standard library, so this adds no
dependency for a development-only path.
"""

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session

router = APIRouter(prefix="/auth/local", tags=["local-dev"])
settings = get_settings()

TOKEN_TTL = timedelta(hours=12)

# Cost parameters, kept modest: this guards a local dev account, not a real one.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
    except ValueError:
        return False

    expected = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt_hex),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    # Constant-time comparison, even here — cheap, and the habit matters.
    return hmac.compare_digest(expected.hex(), digest_hex)


def mint_token(user_id: str, email: str) -> str:
    """Builds a token shaped like Supabase's, so the rest of the API is
    identical in local and hosted modes."""
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "aud": settings.supabase_jwt_audience,
            "iat": now,
            "exp": now + TOKEN_TTL,
            "iss": "blujeansz-local-dev",
        },
        settings.local_auth_secret,
        algorithm="HS256",
    )


class LocalLogin(BaseModel):
    email: EmailStr
    password: str


class LocalLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/login", response_model=LocalLoginResponse)
async def local_login(
    payload: LocalLogin,
    session: AsyncSession = Depends(get_session),
) -> LocalLoginResponse:
    row = (
        await session.execute(
            text(
                """
                select u.id::text as user_id, u.email, d.password_hash
                from auth.users u
                join dev_credentials d on d.user_id = u.id
                where lower(u.email) = lower(:email)
                """
            ),
            {"email": payload.email},
        )
    ).mappings().first()

    # One message for unknown account and wrong password alike.
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password."
    )

    if row is None or not verify_password(payload.password, row["password_hash"]):
        raise invalid

    return LocalLoginResponse(
        access_token=mint_token(row["user_id"], row["email"]),
        expires_in=int(TOKEN_TTL.total_seconds()),
    )
