from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import get_current_profile
from app.models import Author, CaseStudy, Insight, Profile, StaffMember
from app.schemas import DashboardStats, ProfileOut, RecentContentItem

router = APIRouter(tags=["admin"])


@router.get("/me", response_model=ProfileOut)
async def read_current_profile(profile: Profile = Depends(get_current_profile)) -> Profile:
    return profile


@router.get("/dashboard/stats", response_model=DashboardStats)
async def dashboard_stats(
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(get_current_profile),
) -> DashboardStats:
    async def count(model, *conditions) -> int:
        query = select(func.count()).select_from(model)
        for condition in conditions:
            query = query.where(condition)
        return (await session.execute(query)).scalar_one()

    return DashboardStats(
        total_insights=await count(Insight),
        published_insights=await count(Insight, Insight.status == "published"),
        draft_insights=await count(Insight, Insight.status == "draft"),
        case_studies=await count(CaseStudy),
        authors=await count(Author, Author.active.is_(True)),
        team_members=await count(StaffMember, StaffMember.active.is_(True)),
    )


@router.get("/dashboard/recent", response_model=list[RecentContentItem])
async def recent_content(
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(get_current_profile),
    limit: int = 10,
) -> list[RecentContentItem]:
    """Most recently touched content across both types, newest first."""
    insights = (
        await session.execute(select(Insight).order_by(Insight.updated_at.desc()).limit(limit))
    ).unique().scalars().all()

    case_studies = (
        await session.execute(
            select(CaseStudy).order_by(CaseStudy.updated_at.desc()).limit(limit)
        )
    ).unique().scalars().all()

    items = [
        RecentContentItem(
            id=row.id,
            title=row.title,
            slug=row.slug,
            kind="insight",
            author=row.author.display_name if row.author else None,
            status=row.status,
            updated_at=row.updated_at,
        )
        for row in insights
    ] + [
        RecentContentItem(
            id=row.id,
            title=row.title,
            slug=row.slug,
            kind="case_study",
            author=row.client,
            status=row.status,
            updated_at=row.updated_at,
        )
        for row in case_studies
    ]

    items.sort(key=lambda item: item.updated_at, reverse=True)
    return items[:limit]
