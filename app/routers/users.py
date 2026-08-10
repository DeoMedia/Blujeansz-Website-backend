"""CMS user management.

There is no public registration. Administrators invite users through the
Supabase Admin API, which is called server-side with the service-role key —
that key never reaches the browser.

The privilege rules here mirror the guard trigger on `profiles`: nobody may
change their own role, status or publish rights, and only a super admin can
mint another super admin. Both layers enforce it, so bypassing the API still
hits the database rule.
"""

import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.security import ROLE_RANK, require_role
from app.models import Profile
from app.schemas import ProfileOut

router = APIRouter(prefix="/users", tags=["users"])
settings = get_settings()

ASSIGNABLE_ROLES = {"super_admin", "admin", "editor", "author"}


class UserInvite(BaseModel):
    email: EmailStr
    first_name: str | None = None
    last_name: str | None = None
    role: str = Field(default="author")
    can_publish: bool = False


class UserUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    role: str | None = None
    status: str | None = None
    can_publish: bool | None = None


@router.get("", response_model=list[ProfileOut])
async def list_users(
    session: AsyncSession = Depends(get_session),
    actor: Profile = Depends(require_role("admin")),
) -> list[Profile]:
    result = await session.execute(select(Profile).order_by(Profile.created_at.desc()))
    return list(result.scalars().all())


@router.post("/invite", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def invite_user(
    payload: UserInvite,
    session: AsyncSession = Depends(get_session),
    actor: Profile = Depends(require_role("admin")),
) -> Profile:
    if settings.supabase_service_role_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User invitations are not configured on the server.",
        )

    if payload.role not in ASSIGNABLE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown role."
        )

    if payload.role == "super_admin" and actor.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a super admin can grant the super_admin role.",
        )

    base = settings.supabase_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{base}/auth/v1/invite",
            json={
                "email": payload.email,
                "data": {
                    "first_name": payload.first_name,
                    "last_name": payload.last_name,
                    "role": payload.role,
                },
            },
            headers={
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
                "apikey": settings.supabase_service_role_key,
                "Content-Type": "application/json",
            },
        )

    if response.status_code >= 400:
        detail = response.json().get("msg") if response.headers.get(
            "content-type", ""
        ).startswith("application/json") else response.text[:200]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Supabase refused the invitation: {detail}",
        )

    user_id = response.json().get("id")

    # The on_auth_user_created trigger inserts the profile. Read it back and
    # apply the publish flag, which the trigger has no way to know about.
    profile = (
        await session.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()

    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The user was invited but no CMS profile was created.",
        )

    profile.can_publish = payload.can_publish
    await session.commit()
    await session.refresh(profile)
    return profile


@router.patch("/{profile_id}", response_model=ProfileOut)
async def update_user(
    profile_id: uuid.UUID,
    payload: UserUpdate,
    session: AsyncSession = Depends(get_session),
    actor: Profile = Depends(require_role("admin")),
) -> Profile:
    profile = (
        await session.execute(select(Profile).where(Profile.id == profile_id))
    ).scalar_one_or_none()

    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    changes_privileges = (
        payload.role is not None
        or payload.status is not None
        or payload.can_publish is not None
    )

    if changes_privileges:
        # Self-escalation is refused here and again by the database trigger.
        if profile.id == actor.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot change your own role, status or publish rights.",
            )

        # An admin must not be able to demote or edit a super admin.
        if ROLE_RANK[profile.role] > ROLE_RANK[actor.role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot modify an account with more privileges than your own.",
            )

        if payload.role == "super_admin" and actor.role != "super_admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only a super admin can grant the super_admin role.",
            )

    if payload.role is not None:
        if payload.role not in ASSIGNABLE_ROLES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown role."
            )
        profile.role = payload.role

    if payload.status is not None:
        if payload.status not in {"active", "invited", "suspended"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown status."
            )
        profile.status = payload.status

    if payload.can_publish is not None:
        profile.can_publish = payload.can_publish
    if payload.first_name is not None:
        profile.first_name = payload.first_name
    if payload.last_name is not None:
        profile.last_name = payload.last_name

    profile.updated_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(profile)
    return profile


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    actor: Profile = Depends(require_role("super_admin")),
) -> None:
    profile = (
        await session.execute(select(Profile).where(Profile.id == profile_id))
    ).scalar_one_or_none()

    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if profile.id == actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You cannot delete your own account."
        )

    # Remove the auth user too, otherwise the login survives without a profile
    # and the trigger would recreate one on next sign-in.
    if settings.supabase_service_role_key:
        base = settings.supabase_url.rstrip("/")
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.delete(
                f"{base}/auth/v1/admin/users/{profile.user_id}",
                headers={
                    "Authorization": f"Bearer {settings.supabase_service_role_key}",
                    "apikey": settings.supabase_service_role_key,
                },
            )

    await session.delete(profile)
    await session.commit()
