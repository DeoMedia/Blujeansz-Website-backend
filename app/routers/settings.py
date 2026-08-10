"""Site settings.

Key/value rows in `site_settings`. Only rows flagged `is_public` are readable
without authentication — that flag is what keeps an internal setting from being
served to anonymous visitors alongside the public ones.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import get_optional_profile, require_role
from app.models import Profile, SiteSetting

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingOut(BaseModel):
    id: uuid.UUID
    key: str
    value: Any
    description: str | None
    is_public: bool
    updated_at: datetime


class SettingUpsert(BaseModel):
    value: Any
    description: str | None = None
    is_public: bool | None = None


def _serialise(row: SiteSetting) -> SettingOut:
    return SettingOut(
        id=row.id,
        key=row.key,
        value=row.value,
        description=row.description,
        is_public=row.is_public,
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[SettingOut])
async def list_settings(
    session: AsyncSession = Depends(get_session),
    profile: Profile | None = Depends(get_optional_profile),
) -> list[SettingOut]:
    query = select(SiteSetting).order_by(SiteSetting.key)

    # Anonymous callers, and CMS users below admin, see only public rows.
    is_admin = profile is not None and profile.role in {"super_admin", "admin"}
    if not is_admin:
        query = query.where(SiteSetting.is_public.is_(True))

    rows = (await session.execute(query)).scalars().all()
    return [_serialise(row) for row in rows]


@router.put("/{key}", response_model=SettingOut)
async def upsert_setting(
    key: str,
    payload: SettingUpsert,
    session: AsyncSession = Depends(get_session),
    actor: Profile = Depends(require_role("admin")),
) -> SettingOut:
    setting = (
        await session.execute(select(SiteSetting).where(SiteSetting.key == key))
    ).scalar_one_or_none()

    if setting is None:
        setting = SiteSetting(
            id=uuid.uuid4(),
            key=key,
            value=payload.value,
            description=payload.description,
            is_public=payload.is_public if payload.is_public is not None else False,
            updated_by=actor.id,
        )
        session.add(setting)
    else:
        setting.value = payload.value
        if payload.description is not None:
            setting.description = payload.description
        if payload.is_public is not None:
            setting.is_public = payload.is_public
        setting.updated_by = actor.id
        setting.updated_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(setting)
    return _serialise(setting)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_setting(
    key: str,
    session: AsyncSession = Depends(get_session),
    actor: Profile = Depends(require_role("admin")),
) -> None:
    setting = (
        await session.execute(select(SiteSetting).where(SiteSetting.key == key))
    ).scalar_one_or_none()

    if setting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setting not found.")

    await session.delete(setting)
    await session.commit()
