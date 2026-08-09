import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import (
    ROLE_RANK,
    get_current_profile,
    get_optional_profile,
    require_publish_rights,
    require_role,
)
from app.core.slug import unique_slug
from app.models import Author, Insight, InsightCategory, Profile
from app.schemas import InsightDetail, InsightIn, InsightSummary, StatusChange

router = APIRouter(prefix="/insights", tags=["insights"])


def _visible_to_public():
    """The single definition of public visibility, matching the SQL predicate."""
    return (Insight.status == "published") & (Insight.published_at <= func.now())


def _may_see_unpublished(profile: Profile | None) -> bool:
    return profile is not None and ROLE_RANK.get(profile.role, 0) >= ROLE_RANK["editor"]


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


@router.get("", response_model=list[InsightSummary])
async def list_insights(
    session: AsyncSession = Depends(get_session),
    category: str | None = Query(default=None, description="Category slug"),
    author: str | None = Query(default=None, description="Author slug"),
    featured: bool | None = None,
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[Insight]:
    query = select(Insight).where(_visible_to_public())

    if category:
        query = query.join(InsightCategory).where(InsightCategory.slug == category)
    if author:
        query = query.join(Author, Insight.author_id == Author.id).where(Author.slug == author)
    if featured is not None:
        query = query.where(Insight.featured.is_(featured))

    query = query.order_by(Insight.published_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(query)).unique().scalars().all())


@router.get("/{slug}", response_model=InsightDetail)
async def get_insight(
    slug: str,
    session: AsyncSession = Depends(get_session),
    profile: Profile | None = Depends(get_optional_profile),
) -> Insight:
    """Fetches one article by slug.

    Editors and above may read unpublished articles here — this is what backs
    admin preview, so drafts render through the real public template rather
    than a separate preview view. Anonymous callers only ever see published.
    """
    query = select(Insight).where(Insight.slug == slug)
    if not _may_see_unpublished(profile):
        query = query.where(_visible_to_public())

    insight = (await session.execute(query)).unique().scalar_one_or_none()
    if insight is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found.")

    return insight


@router.get("/{slug}/related", response_model=list[InsightSummary])
async def related_insights(
    slug: str,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=3, ge=1, le=12),
) -> list[Insight]:
    current = (
        await session.execute(select(Insight).where(Insight.slug == slug, _visible_to_public()))
    ).unique().scalar_one_or_none()

    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found.")

    same_category = []
    if current.category_id:
        same_category = list(
            (
                await session.execute(
                    select(Insight)
                    .where(
                        _visible_to_public(),
                        Insight.category_id == current.category_id,
                        Insight.id != current.id,
                    )
                    .order_by(Insight.published_at.desc())
                    .limit(limit)
                )
            )
            .unique()
            .scalars()
            .all()
        )

    if len(same_category) >= limit:
        return same_category

    # Top up with the most recent articles so the section is never half empty
    # when a category is thin.
    seen = {current.id, *(row.id for row in same_category)}
    filler = list(
        (
            await session.execute(
                select(Insight)
                .where(_visible_to_public(), Insight.id.notin_(seen))
                .order_by(Insight.published_at.desc())
                .limit(limit - len(same_category))
            )
        )
        .unique()
        .scalars()
        .all()
    )

    return [*same_category, *filler]


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


@router.get("/admin/all", response_model=list[InsightSummary])
async def list_all_insights(
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(get_current_profile),
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = None,
) -> list[Insight]:
    query = select(Insight)

    # Authors see their own work plus anything already public; editors and
    # above see everything. This mirrors the RLS policy on the table.
    if not _may_see_unpublished(profile):
        query = query.where((Insight.created_by == profile.id) | _visible_to_public())

    if status_filter:
        query = query.where(Insight.status == status_filter)
    if search:
        query = query.where(Insight.title.ilike(f"%{search}%"))

    query = query.order_by(Insight.updated_at.desc())
    return list((await session.execute(query)).unique().scalars().all())


@router.get("/by-id/{insight_id}", response_model=InsightDetail)
async def get_insight_by_id(
    insight_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(get_current_profile),
) -> Insight:
    """Loads a single article for editing.

    The editor needs the full body, which the listing endpoints omit, and it
    knows the row by id rather than slug — a slug lookup would break the moment
    an editor changes the slug.
    """
    insight = (
        await session.execute(select(Insight).where(Insight.id == insight_id))
    ).unique().scalar_one_or_none()

    if insight is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found.")

    if not _may_see_unpublished(profile) and insight.created_by != profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only open articles you created.",
        )

    return insight


@router.post("", response_model=InsightDetail, status_code=status.HTTP_201_CREATED)
async def create_insight(
    payload: InsightIn,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("author")),
) -> Insight:
    slug = payload.slug or await unique_slug(session, Insight, payload.title)

    insight = Insight(
        id=uuid.uuid4(),
        **payload.model_dump(exclude={"slug", "content"}),
        slug=slug,
        content=[block.model_dump() for block in payload.content],
        status="draft",
        created_by=profile.id,
        updated_by=profile.id,
    )

    session.add(insight)
    await session.commit()
    await session.refresh(insight)
    return insight


async def _load_for_edit(session: AsyncSession, insight_id: uuid.UUID, profile: Profile) -> Insight:
    insight = (
        await session.execute(select(Insight).where(Insight.id == insight_id))
    ).unique().scalar_one_or_none()

    if insight is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found.")

    # Authors may only touch their own drafts.
    if not _may_see_unpublished(profile) and insight.created_by != profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit articles you created.",
        )

    return insight


@router.put("/{insight_id}", response_model=InsightDetail)
async def update_insight(
    insight_id: uuid.UUID,
    payload: InsightIn,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(get_current_profile),
) -> Insight:
    insight = await _load_for_edit(session, insight_id, profile)

    data = payload.model_dump(exclude={"slug", "content"})
    for field, value in data.items():
        setattr(insight, field, value)

    insight.content = [block.model_dump() for block in payload.content]
    if payload.slug and payload.slug != insight.slug:
        insight.slug = await unique_slug(session, Insight, payload.slug, exclude_id=insight.id)

    insight.updated_by = profile.id

    await session.commit()
    await session.refresh(insight)
    return insight


@router.post("/{insight_id}/status", response_model=InsightDetail)
async def change_status(
    insight_id: uuid.UUID,
    payload: StatusChange,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(get_current_profile),
) -> Insight:
    insight = await _load_for_edit(session, insight_id, profile)

    # Publishing is a distinct privilege from editing.
    if payload.status in {"published", "scheduled"}:
        require_publish_rights(profile)

    if payload.status == "scheduled":
        if payload.scheduled_at is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="scheduled_at is required when scheduling.",
            )
        insight.scheduled_at = payload.scheduled_at

    if payload.status == "published" and insight.published_at is None:
        # Stamp on first publish only, so re-publishing keeps the original date.
        insight.published_at = datetime.now(timezone.utc)

    insight.status = payload.status
    insight.updated_by = profile.id

    await session.commit()
    await session.refresh(insight)
    return insight


@router.delete("/{insight_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_insight(
    insight_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("admin")),
) -> None:
    insight = (
        await session.execute(select(Insight).where(Insight.id == insight_id))
    ).unique().scalar_one_or_none()

    if insight is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found.")

    await session.delete(insight)
    await session.commit()
