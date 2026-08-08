"""Request and response models.

Content blocks are validated as a discriminated union so a malformed block is
rejected at the API boundary rather than reaching the database or, worse, the
renderer. This mirrors `parseContentBlocks` in the frontend.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator

# ---------------------------------------------------------------------------
# Content blocks
# ---------------------------------------------------------------------------


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"]
    text: str = Field(min_length=1)
    variant: Literal["lead", "default"] = "default"


class HeadingBlock(BaseModel):
    type: Literal["heading"]
    level: Literal[2, 3] = 2
    text: str = Field(min_length=1)


class ImageBlock(BaseModel):
    type: Literal["image"]
    url: str = Field(min_length=1)
    alt: str = ""
    caption: str | None = None


class QuoteBlock(BaseModel):
    type: Literal["quote"]
    text: str = Field(min_length=1)
    attribution: str | None = None


class PullQuoteBlock(BaseModel):
    type: Literal["pull_quote"]
    text: str = Field(min_length=1)


class ListBlock(BaseModel):
    type: Literal["bullet_list", "numbered_list"]
    items: list[str] = Field(min_length=1)


class DividerBlock(BaseModel):
    type: Literal["divider"]


class CtaBlock(BaseModel):
    type: Literal["cta"]
    label: str = Field(min_length=1)
    href: str = Field(min_length=1)
    description: str | None = None


ContentBlock = Annotated[
    ParagraphBlock
    | HeadingBlock
    | ImageBlock
    | QuoteBlock
    | PullQuoteBlock
    | ListBlock
    | DividerBlock
    | CtaBlock,
    Field(discriminator="type"),
]

ContentStatus = Literal["draft", "in_review", "scheduled", "published", "archived"]


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    display_order: int
    active: bool


class AuthorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    display_name: str
    slug: str
    job_title: str | None
    short_bio: str | None
    full_bio: str | None
    profile_image_url: str | None
    linkedin_url: str | None
    instagram_url: str | None
    website_url: str | None
    active: bool


class StaffMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    slug: str
    job_title: str | None
    department: str | None
    short_bio: str | None
    full_bio: str | None
    profile_image_url: str | None
    linkedin_url: str | None
    instagram_url: str | None
    website_url: str | None
    display_order: int
    featured: bool
    active: bool


class StaffMemberIn(BaseModel):
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    slug: str | None = None
    job_title: str | None = None
    department: str | None = None
    short_bio: str | None = None
    full_bio: str | None = None
    profile_image_url: str | None = None
    email: EmailStr | None = None
    linkedin_url: HttpUrl | None = None
    instagram_url: HttpUrl | None = None
    website_url: HttpUrl | None = None
    display_order: int = 0
    featured: bool = False
    active: bool = True


class AuthorIn(BaseModel):
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    display_name: str | None = None
    slug: str | None = None
    job_title: str | None = None
    short_bio: str | None = None
    full_bio: str | None = None
    profile_image_url: str | None = None
    email: EmailStr | None = None
    linkedin_url: HttpUrl | None = None
    instagram_url: HttpUrl | None = None
    website_url: HttpUrl | None = None
    active: bool = True


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


class InsightSummary(BaseModel):
    """Listing payload — deliberately excludes `content`.

    The listing renders cards; shipping every article body would multiply the
    response size for data the page never reads.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    slug: str
    excerpt: str | None
    featured_image_url: str | None
    featured_image_alt: str | None
    read_time_minutes: int | None
    status: ContentStatus
    featured: bool
    published_at: datetime | None
    updated_at: datetime
    category: CategoryOut | None = None
    author: AuthorOut | None = None


class InsightDetail(InsightSummary):
    content: list[ContentBlock]
    hero_image_position: str
    seo_title: str | None
    meta_description: str | None
    canonical_url: str | None
    og_title: str | None
    og_description: str | None
    og_image_url: str | None
    scheduled_at: datetime | None
    created_at: datetime


class InsightIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    slug: str | None = Field(default=None, max_length=300)
    excerpt: str | None = None
    content: list[ContentBlock] = Field(default_factory=list)
    category_id: uuid.UUID | None = None
    author_id: uuid.UUID | None = None
    featured_image_url: str | None = None
    featured_image_alt: str | None = None
    hero_image_position: str = "center"
    read_time_minutes: int | None = Field(default=None, ge=1, le=600)
    featured: bool = False

    seo_title: str | None = Field(default=None, max_length=200)
    meta_description: str | None = Field(default=None, max_length=400)
    canonical_url: str | None = None
    og_title: str | None = None
    og_description: str | None = None
    og_image_url: str | None = None


class StatusChange(BaseModel):
    status: ContentStatus
    scheduled_at: datetime | None = None

    @field_validator("scheduled_at")
    @classmethod
    def _scheduled_must_be_future(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        # A "scheduled" time in the past would sit unpublished until the next
        # sweep and confuse the editor; reject it at the boundary.
        from datetime import timezone

        now = datetime.now(timezone.utc)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if value <= now:
            raise ValueError("scheduled_at must be in the future")
        return value


# ---------------------------------------------------------------------------
# Case studies
# ---------------------------------------------------------------------------


class MetricIn(BaseModel):
    value: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = None


class MetricOut(MetricIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_order: int


class CaseStudySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    slug: str
    client: str | None
    industry: str | None
    project_year: int | None
    location: str | None
    summary: str | None
    featured_image_url: str | None
    status: ContentStatus
    featured: bool
    published_at: datetime | None
    updated_at: datetime
    metrics: list[MetricOut] = Field(default_factory=list)


class CaseStudyDetail(CaseStudySummary):
    hero_image_url: str | None
    challenge: list[ContentBlock]
    strategic_approach: list[ContentBlock]
    solution: list[ContentBlock]
    execution: list[ContentBlock]
    results_summary: list[ContentBlock]
    client_quote: str | None
    quote_attribution: str | None
    seo_title: str | None
    meta_description: str | None
    og_image_url: str | None
    scheduled_at: datetime | None
    created_at: datetime


class CaseStudyIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    slug: str | None = None
    client: str | None = None
    industry: str | None = None
    project_year: int | None = Field(default=None, ge=1900, le=2200)
    location: str | None = None
    summary: str | None = None
    featured_image_url: str | None = None
    hero_image_url: str | None = None
    challenge: list[ContentBlock] = Field(default_factory=list)
    strategic_approach: list[ContentBlock] = Field(default_factory=list)
    solution: list[ContentBlock] = Field(default_factory=list)
    execution: list[ContentBlock] = Field(default_factory=list)
    results_summary: list[ContentBlock] = Field(default_factory=list)
    client_quote: str | None = None
    quote_attribution: str | None = None
    featured: bool = False
    seo_title: str | None = None
    meta_description: str | None = None
    og_image_url: str | None = None
    metrics: list[MetricIn] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Media, profile, dashboard
# ---------------------------------------------------------------------------


class MediaAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_name: str
    storage_path: str
    public_url: str
    mime_type: str
    file_size: int
    width: int | None
    height: int | None
    alt_text: str | None
    caption: str | None
    created_at: datetime


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str | None
    last_name: str | None
    email: str
    avatar_url: str | None
    role: str
    status: str
    can_publish: bool
    last_login_at: datetime | None


class DashboardStats(BaseModel):
    total_insights: int
    published_insights: int
    draft_insights: int
    case_studies: int
    authors: int
    team_members: int


class RecentContentItem(BaseModel):
    id: uuid.UUID
    title: str
    slug: str
    kind: Literal["insight", "case_study"]
    author: str | None
    status: ContentStatus
    updated_at: datetime
