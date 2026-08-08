"""Media uploads.

Files go to Supabase Storage over its REST API using the service-role key,
which never leaves this service. The browser uploads to FastAPI, not directly
to Storage, so size and type are enforced server-side where they cannot be
bypassed by a modified client.
"""

import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.security import ROLE_RANK, get_current_profile, require_role
from app.models import MediaAsset, Profile
from app.schemas import MediaAssetOut

router = APIRouter(prefix="/media", tags=["media"])
settings = get_settings()

BUCKET = "media"
MAX_BYTES = 10 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def _storage_key(file_name: str, content_type: str) -> str:
    now = datetime.now(timezone.utc)
    stem = file_name.rsplit(".", 1)[0].lower()
    safe_stem = "".join(char if char.isalnum() else "-" for char in stem).strip("-") or "image"
    extension = EXTENSIONS[content_type]
    return f"{now:%Y/%m}/{uuid.uuid4()}-{safe_stem}.{extension}"


@router.get("", response_model=list[MediaAssetOut])
async def list_media(
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(get_current_profile),
    limit: int = 100,
) -> list[MediaAsset]:
    result = await session.execute(
        select(MediaAsset).order_by(MediaAsset.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


@router.post("", response_model=MediaAssetOut, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    alt_text: str | None = Form(default=None),
    caption: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("author")),
) -> MediaAsset:
    if settings.supabase_service_role_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Media storage is not configured on the server.",
        )

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPG, PNG and WebP images are accepted.",
        )

    payload = await file.read()
    if len(payload) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File is {len(payload) / 1024 / 1024:.1f} MB — the limit is 10 MB.",
        )
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The file is empty."
        )

    key = _storage_key(file.filename or "image", file.content_type)
    base = settings.supabase_url.rstrip("/")

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{base}/storage/v1/object/{BUCKET}/{key}",
            content=payload,
            headers={
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
                "Content-Type": file.content_type,
                "x-upsert": "false",
            },
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Storage rejected the upload: {response.text[:200]}",
        )

    asset = MediaAsset(
        id=uuid.uuid4(),
        file_name=file.filename or key.rsplit("/", 1)[-1],
        storage_path=key,
        public_url=f"{base}/storage/v1/object/public/{BUCKET}/{key}",
        mime_type=file.content_type,
        file_size=len(payload),
        alt_text=alt_text,
        caption=caption,
        uploaded_by=profile.id,
    )

    session.add(asset)
    try:
        await session.commit()
    except Exception:
        # Do not leave an orphaned object behind if the row cannot be written.
        await session.rollback()
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.delete(
                f"{base}/storage/v1/object/{BUCKET}/{key}",
                headers={"Authorization": f"Bearer {settings.supabase_service_role_key}"},
            )
        raise

    await session.refresh(asset)
    return asset


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(
    asset_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(get_current_profile),
) -> None:
    asset = (
        await session.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one_or_none()

    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")

    # Uploaders may remove their own files; admins may remove anything.
    is_admin = ROLE_RANK.get(profile.role, 0) >= ROLE_RANK["admin"]
    if asset.uploaded_by != profile.id and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete files you uploaded.",
        )

    base = settings.supabase_url.rstrip("/")
    if settings.supabase_service_role_key:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.delete(
                f"{base}/storage/v1/object/{BUCKET}/{asset.storage_path}",
                headers={"Authorization": f"Bearer {settings.supabase_service_role_key}"},
            )

    await session.delete(asset)
    await session.commit()
