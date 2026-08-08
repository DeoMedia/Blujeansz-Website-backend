"""Slug generation, mirroring public.slugify() in SQL and slugify() in the
frontend so all three produce the same value for the same title."""

import re
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_REPEATED_DASH = re.compile(r"-{2,}")


def slugify(value: str) -> str:
    normalised = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in normalised if not unicodedata.combining(char))
    lowered = _NON_ALNUM.sub("-", stripped.lower())
    return _REPEATED_DASH.sub("-", lowered).strip("-")


async def unique_slug(
    session: AsyncSession,
    model,
    value: str,
    *,
    exclude_id=None,
) -> str:
    """Returns a slug for `value` that no other row of `model` holds.

    The database also has a unique constraint on slug — this just avoids the
    round trip of hitting it and gives the editor a usable suggestion.
    """
    root = slugify(value) or "untitled"
    candidate = root
    suffix = 2

    while True:
        query = select(func.count()).select_from(model).where(model.slug == candidate)
        if exclude_id is not None:
            query = query.where(model.id != exclude_id)

        collisions = (await session.execute(query)).scalar_one()
        if collisions == 0:
            return candidate

        candidate = f"{root}-{suffix}"
        suffix += 1
