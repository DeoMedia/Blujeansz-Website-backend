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
from app.models import CaseStudy, CaseStudyMetric, Profile
from app.schemas import CaseStudyDetail, CaseStudyIn, CaseStudySummary, StatusChange

router = APIRouter(prefix="/case-studies", tags=["case-studies"])

BLOCK_FIELDS = ("challenge", "strategic_approach", "solution", "execution", "results_summary")


def _visible_to_public():
    return (CaseStudy.status == "published") & (CaseStudy.published_at <= func.now())


def _may_see_unpublished(profile: Profile | None) -> bool:
    return profile is not None and ROLE_RANK.get(profile.role, 0) >= ROLE_RANK["editor"]


async def _replace_metrics(session: AsyncSession, case_study: CaseStudy, payload) -> None:
    """Metrics are free-form and reorderable, so the set is replaced wholesale
    rather than diffed — display_order stays authoritative and removed rows
    cannot linger."""
    case_study.metrics.clear()
    await session.flush()

    for index, metric in enumerate(payload):
        case_study.metrics.append(
            CaseStudyMetric(
                id=uuid.uuid4(),
                value=metric.value,
                label=metric.label,
                description=metric.description,
                display_order=index,
            )
        )


@router.get("", response_model=list[CaseStudySummary])
async def list_case_studies(
    session: AsyncSession = Depends(get_session),
    featured: bool | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[CaseStudy]:
    query = select(CaseStudy).where(_visible_to_public())
    if featured is not None:
        query = query.where(CaseStudy.featured.is_(featured))

    query = query.order_by(CaseStudy.published_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(query)).unique().scalars().all())


@router.get("/{slug}", response_model=CaseStudyDetail)
async def get_case_study(
    slug: str,
    session: AsyncSession = Depends(get_session),
    profile: Profile | None = Depends(get_optional_profile),
) -> CaseStudy:
    query = select(CaseStudy).where(CaseStudy.slug == slug)
    if not _may_see_unpublished(profile):
        query = query.where(_visible_to_public())

    case_study = (await session.execute(query)).unique().scalar_one_or_none()
    if case_study is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case study not found.")

    return case_study


@router.get("/admin/all", response_model=list[CaseStudySummary])
async def list_all_case_studies(
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("editor")),
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = None,
) -> list[CaseStudy]:
    query = select(CaseStudy)
    if status_filter:
        query = query.where(CaseStudy.status == status_filter)
    if search:
        query = query.where(CaseStudy.title.ilike(f"%{search}%"))

    query = query.order_by(CaseStudy.updated_at.desc())
    return list((await session.execute(query)).unique().scalars().all())


@router.post("", response_model=CaseStudyDetail, status_code=status.HTTP_201_CREATED)
async def create_case_study(
    payload: CaseStudyIn,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("editor")),
) -> CaseStudy:
    slug = payload.slug or await unique_slug(session, CaseStudy, payload.title)

    data = payload.model_dump(exclude={"slug", "metrics", *BLOCK_FIELDS})
    case_study = CaseStudy(
        id=uuid.uuid4(),
        slug=slug,
        status="draft",
        created_by=profile.id,
        updated_by=profile.id,
        **data,
        **{field: [b.model_dump() for b in getattr(payload, field)] for field in BLOCK_FIELDS},
    )

    session.add(case_study)
    await session.flush()
    await _replace_metrics(session, case_study, payload.metrics)

    await session.commit()
    await session.refresh(case_study)
    return case_study


@router.put("/{case_study_id}", response_model=CaseStudyDetail)
async def update_case_study(
    case_study_id: uuid.UUID,
    payload: CaseStudyIn,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("editor")),
) -> CaseStudy:
    case_study = (
        await session.execute(select(CaseStudy).where(CaseStudy.id == case_study_id))
    ).unique().scalar_one_or_none()

    if case_study is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case study not found.")

    for field, value in payload.model_dump(
        exclude={"slug", "metrics", *BLOCK_FIELDS}
    ).items():
        setattr(case_study, field, value)

    for field in BLOCK_FIELDS:
        setattr(case_study, field, [b.model_dump() for b in getattr(payload, field)])

    if payload.slug and payload.slug != case_study.slug:
        case_study.slug = await unique_slug(
            session, CaseStudy, payload.slug, exclude_id=case_study.id
        )

    case_study.updated_by = profile.id
    await _replace_metrics(session, case_study, payload.metrics)

    await session.commit()
    await session.refresh(case_study)
    return case_study


@router.post("/{case_study_id}/status", response_model=CaseStudyDetail)
async def change_status(
    case_study_id: uuid.UUID,
    payload: StatusChange,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("editor")),
) -> CaseStudy:
    case_study = (
        await session.execute(select(CaseStudy).where(CaseStudy.id == case_study_id))
    ).unique().scalar_one_or_none()

    if case_study is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case study not found.")

    if payload.status in {"published", "scheduled"}:
        require_publish_rights(profile)

    if payload.status == "scheduled":
        if payload.scheduled_at is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="scheduled_at is required when scheduling.",
            )
        case_study.scheduled_at = payload.scheduled_at

    if payload.status == "published" and case_study.published_at is None:
        case_study.published_at = datetime.now(timezone.utc)

    case_study.status = payload.status
    case_study.updated_by = profile.id

    await session.commit()
    await session.refresh(case_study)
    return case_study


@router.delete("/{case_study_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case_study(
    case_study_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    profile: Profile = Depends(require_role("admin")),
) -> None:
    case_study = (
        await session.execute(select(CaseStudy).where(CaseStudy.id == case_study_id))
    ).unique().scalar_one_or_none()

    if case_study is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case study not found.")

    await session.delete(case_study)
    await session.commit()
