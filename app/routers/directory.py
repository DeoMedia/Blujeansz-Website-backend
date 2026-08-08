"""Authors, staff, categories and services — the reference data behind the
public About page and the editor's dropdowns."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import require_role
from app.core.slug import unique_slug
from app.models import Author, InsightCategory, Profile, Service, StaffMember
from app.schemas import AuthorIn, AuthorOut, CategoryOut, StaffMemberIn, StaffMemberOut

router = APIRouter(tags=["directory"])


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(session: AsyncSession = Depends(get_session)) -> list[InsightCategory]:
    result = await session.execute(
        select(InsightCategory)
        .where(InsightCategory.active.is_(True))
        .order_by(InsightCategory.display_order)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------


@router.get("/staff", response_model=list[StaffMemberOut])
async def list_staff(session: AsyncSession = Depends(get_session)) -> list[StaffMember]:
    """The About page team grid: active members in display order."""
    result = await session.execute(
        select(StaffMember)
        .where(StaffMember.active.is_(True))
        .order_by(StaffMember.display_order)
    )
    return list(result.scalars().all())


@router.get("/staff/{slug}", response_model=StaffMemberOut)
async def get_staff_member(
    slug: str, session: AsyncSession = Depends(get_session)
) -> StaffMember:
    member = (
        await session.execute(
            select(StaffMember).where(StaffMember.slug == slug, StaffMember.active.is_(True))
        )
    ).scalar_one_or_none()

    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team member not found.")
    return member


@router.post("/staff", response_model=StaffMemberOut, status_code=status.HTTP_201_CREATED)
async def create_staff_member(
    payload: StaffMemberIn,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("admin")),
) -> StaffMember:
    full_name = f"{payload.first_name} {payload.last_name}"
    slug = payload.slug or await unique_slug(session, StaffMember, full_name)

    data = payload.model_dump(exclude={"slug"}, mode="json")
    member = StaffMember(id=uuid.uuid4(), slug=slug, **data)

    session.add(member)
    await session.commit()
    await session.refresh(member)
    return member


@router.put("/staff/{member_id}", response_model=StaffMemberOut)
async def update_staff_member(
    member_id: uuid.UUID,
    payload: StaffMemberIn,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("admin")),
) -> StaffMember:
    member = (
        await session.execute(select(StaffMember).where(StaffMember.id == member_id))
    ).scalar_one_or_none()

    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team member not found.")

    for field, value in payload.model_dump(exclude={"slug"}, mode="json").items():
        setattr(member, field, value)

    if payload.slug and payload.slug != member.slug:
        member.slug = await unique_slug(session, StaffMember, payload.slug, exclude_id=member.id)

    await session.commit()
    await session.refresh(member)
    return member


@router.delete("/staff/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff_member(
    member_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("admin")),
) -> None:
    member = (
        await session.execute(select(StaffMember).where(StaffMember.id == member_id))
    ).scalar_one_or_none()

    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team member not found.")

    await session.delete(member)
    await session.commit()


# ---------------------------------------------------------------------------
# Authors
# ---------------------------------------------------------------------------


@router.get("/authors", response_model=list[AuthorOut])
async def list_authors(session: AsyncSession = Depends(get_session)) -> list[Author]:
    result = await session.execute(
        select(Author).where(Author.active.is_(True)).order_by(Author.display_name)
    )
    return list(result.scalars().all())


@router.get("/authors/{slug}", response_model=AuthorOut)
async def get_author(slug: str, session: AsyncSession = Depends(get_session)) -> Author:
    author = (
        await session.execute(
            select(Author).where(Author.slug == slug, Author.active.is_(True))
        )
    ).scalar_one_or_none()

    if author is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Author not found.")
    return author


@router.post("/authors", response_model=AuthorOut, status_code=status.HTTP_201_CREATED)
async def create_author(
    payload: AuthorIn,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("admin")),
) -> Author:
    display_name = payload.display_name or f"{payload.first_name} {payload.last_name}"
    slug = payload.slug or await unique_slug(session, Author, display_name)

    data = payload.model_dump(exclude={"slug", "display_name"}, mode="json")
    author = Author(id=uuid.uuid4(), slug=slug, display_name=display_name, **data)

    session.add(author)
    await session.commit()
    await session.refresh(author)
    return author


@router.put("/authors/{author_id}", response_model=AuthorOut)
async def update_author(
    author_id: uuid.UUID,
    payload: AuthorIn,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("admin")),
) -> Author:
    author = (
        await session.execute(select(Author).where(Author.id == author_id))
    ).scalar_one_or_none()

    if author is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Author not found.")

    data = payload.model_dump(exclude={"slug", "display_name"}, mode="json")
    for field, value in data.items():
        setattr(author, field, value)

    if payload.display_name:
        author.display_name = payload.display_name
    if payload.slug and payload.slug != author.slug:
        author.slug = await unique_slug(session, Author, payload.slug, exclude_id=author.id)

    await session.commit()
    await session.refresh(author)
    return author


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@router.get("/services")
async def list_services(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(
        select(Service).where(Service.active.is_(True)).order_by(Service.display_order)
    )
    return [
        {
            "id": str(service.id),
            "name": service.name,
            "slug": service.slug,
            "short_description": service.short_description,
            "description": service.description,
            "icon": service.icon,
        }
        for service in result.scalars().all()
    ]
