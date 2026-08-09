"""Converts the BLU JEANSZ editorial collection (.docx) into CMS content.

The Word document uses semantic styles that map directly onto the content-block
model, so the conversion is deterministic rather than a hand transcription:

    Label         "JANUARY  /  Cultural Intelligence"  -> month + category
    Title         article title
    (plain)       "INSIGHT 01" marker, then body paragraphs
    Heading2      section heading      -> heading, level 2
    PullQuote     emphasis line        -> pull_quote
    FinalThought  closing statement    -> paragraph

Emits a SQL seed migration and, optionally, a TypeScript data module so the
articles render before the database is provisioned.

Usage:
    python scripts/import_editorial_docx.py <input.docx> \
        --sql supabase/migrations/20260808000006_seed_insights.sql \
        --ts  ../Blujeansz-Website/src/app/data/editorial.generated.ts
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

MONTHS = {
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4,
    "MAY": 5, "JUNE": 6, "JULY": 7, "AUGUST": 8,
    "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11, "DECEMBER": 12,
}

# The document writes "Global x Local"; the seeded category is "Global × Local".
CATEGORY_ALIASES = {"global x local": "global-local"}

FOOTER = "BLU JEANSZ"
WORDS_PER_MINUTE = 220
PUBLISH_YEAR = 2026
PUBLISH_DAY = 5


def slugify(value: str) -> str:
    normalised = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in normalised if not unicodedata.combining(c))
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", stripped.lower())).strip("-")


def sql_quote(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


@dataclass
class Article:
    index: int
    title: str
    slug: str
    category_slug: str
    excerpt: str
    blocks: list[dict] = field(default_factory=list)
    published_at: datetime | None = None
    status: str = "draft"
    read_time: int = 1


def read_paragraphs(path: Path) -> list[tuple[str, str]]:
    root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
    out: list[tuple[str, str]] = []

    for p in root.iter(f"{W}p"):
        style = ""
        pPr = p.find(f"{W}pPr")
        if pPr is not None:
            node = pPr.find(f"{W}pStyle")
            if node is not None:
                style = node.get(f"{W}val", "")

        text = "".join(t.text or "" for t in p.iter(f"{W}t")).strip()
        # Word uses non-breaking spaces liberally in this document.
        text = text.replace(" ", " ")
        text = re.sub(r"\s{2,}", " ", text)
        out.append((style, text))

    return out


def parse(paragraphs: list[tuple[str, str]]) -> list[Article]:
    starts = [i for i, (style, _) in enumerate(paragraphs) if style == "Label"]
    articles: list[Article] = []

    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(paragraphs)
        chunk = paragraphs[start:end]

        label = chunk[0][1]
        month_name, _, category_name = label.partition("/")
        month = MONTHS[month_name.strip().upper()]

        title = next(text for style, text in chunk if style == "Title")
        category_slug = CATEGORY_ALIASES.get(
            category_name.strip().lower(), slugify(category_name)
        )

        blocks: list[dict] = []
        lead_taken = False

        for style, text in chunk:
            if not text or style in {"Label", "Title"}:
                continue
            # "INSIGHT 01" index markers and the footer strapline are layout,
            # not content.
            if re.fullmatch(r"INSIGHT \d+", text) or text.startswith(FOOTER):
                continue

            if style == "Heading2":
                blocks.append({"type": "heading", "level": 2, "text": text})
            elif style == "PullQuote":
                blocks.append({"type": "pull_quote", "text": text.strip("“”\"")})
            elif style == "FinalThought":
                blocks.append({"type": "paragraph", "text": text})
            else:
                if not lead_taken:
                    blocks.append({"type": "paragraph", "variant": "lead", "text": text})
                    lead_taken = True
                else:
                    blocks.append({"type": "paragraph", "text": text})

        excerpt = next(
            (b["text"] for b in blocks if b.get("variant") == "lead"),
            "",
        )

        words = sum(
            len(b.get("text", "").split())
            for b in blocks
            if b["type"] in {"paragraph", "heading", "pull_quote"}
        )

        published = datetime(
            PUBLISH_YEAR, month, PUBLISH_DAY, 9, 0, tzinfo=timezone.utc
        )
        # A future-dated article must not be published: the public visibility
        # rule is published_at <= now(), so it would simply never appear.
        # Scheduling it is what the editorial calendar actually describes.
        status = "published" if published <= datetime.now(timezone.utc) else "scheduled"

        articles.append(
            Article(
                index=position + 1,
                title=title,
                slug=slugify(title),
                category_slug=category_slug,
                excerpt=excerpt,
                blocks=blocks,
                published_at=published,
                status=status,
                read_time=max(1, round(words / WORDS_PER_MINUTE)),
            )
        )

    return articles


def render_sql(articles: list[Article]) -> str:
    lines = [
        "-- =============================================================================",
        "-- BLUJEANSZ CMS — 2026 editorial collection",
        "-- =============================================================================",
        "-- Generated by scripts/import_editorial_docx.py from the source .docx.",
        "-- Do not hand-edit; re-run the script instead.",
        "--",
        "-- Articles dated after the seed date are inserted as 'scheduled' rather than",
        "-- 'published' — public reads require published_at <= now(), so a future-dated",
        "-- 'published' row would never become visible. publish_due_content() promotes",
        "-- them when their date arrives.",
        "-- =============================================================================",
        "",
    ]

    for article in articles:
        content = json.dumps(article.blocks, ensure_ascii=False)
        published = (
            sql_quote(article.published_at.isoformat())
            if article.status == "published"
            else "NULL"
        )
        scheduled = (
            sql_quote(article.published_at.isoformat())
            if article.status == "scheduled"
            else "NULL"
        )

        lines += [
            f"-- {article.index:02d}. {article.title}",
            "insert into public.insights (",
            "  title, slug, excerpt, content, category_id, status,",
            "  read_time_minutes, featured, seo_title, meta_description,",
            "  published_at, scheduled_at",
            ") values (",
            f"  {sql_quote(article.title)},",
            f"  {sql_quote(article.slug)},",
            f"  {sql_quote(article.excerpt)},",
            f"  {sql_quote(content)}::jsonb,",
            f"  (select id from public.insight_categories where slug = {sql_quote(article.category_slug)}),",
            f"  {sql_quote(article.status)},",
            f"  {article.read_time},",
            f"  {sql_quote(article.index <= 3)},",
            f"  {sql_quote(article.title)},",
            f"  {sql_quote(article.excerpt[:300])},",
            f"  {published}::timestamptz,",
            f"  {scheduled}::timestamptz",
            ")",
            "on conflict (slug) do update set",
            "  title = excluded.title,",
            "  excerpt = excluded.excerpt,",
            "  content = excluded.content,",
            "  category_id = excluded.category_id,",
            "  read_time_minutes = excluded.read_time_minutes,",
            "  updated_at = now();",
            "",
        ]

    return "\n".join(lines)


def render_ts(articles: list[Article]) -> str:
    payload = [
        {
            "title": a.title,
            "slug": a.slug,
            "categorySlug": a.category_slug,
            "excerpt": a.excerpt,
            "readTimeMinutes": a.read_time,
            "publishedAt": a.published_at.isoformat() if a.published_at else None,
            "status": a.status,
            "featured": a.index <= 3,
            "blocks": a.blocks,
        }
        for a in articles
    ]

    body = json.dumps(payload, indent=2, ensure_ascii=False)

    return (
        "/* eslint-disable */\n"
        "// GENERATED FILE — do not edit.\n"
        "// Source: BLU_JEANSZ_2026_Insights_12_Blog_Posts.docx\n"
        "// Regenerate: python scripts/import_editorial_docx.py <docx> --ts <path>\n"
        "//\n"
        "// This is the interim content source. Once the CMS database is live the\n"
        "// same articles are served from the API and this module can be deleted.\n"
        "\n"
        'import type { ContentBlock } from "../types/content";\n'
        "\n"
        "export interface EditorialArticle {\n"
        "  title: string;\n"
        "  slug: string;\n"
        "  categorySlug: string;\n"
        "  excerpt: string;\n"
        "  readTimeMinutes: number;\n"
        "  publishedAt: string | null;\n"
        '  status: "published" | "scheduled";\n'
        "  featured: boolean;\n"
        "  blocks: ContentBlock[];\n"
        "}\n"
        "\n"
        f"export const editorialArticles: EditorialArticle[] = {body};\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path)
    parser.add_argument("--sql", type=Path)
    parser.add_argument("--ts", type=Path)
    args = parser.parse_args()

    articles = parse(read_paragraphs(args.docx))

    print(f"Parsed {len(articles)} articles:")
    for a in articles:
        counts: dict[str, int] = {}
        for block in a.blocks:
            counts[block["type"]] = counts.get(block["type"], 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        print(f"  {a.index:02d} {a.status:9} {a.read_time:>2}min  {a.slug[:52]:52} {summary}")

    if args.sql:
        args.sql.parent.mkdir(parents=True, exist_ok=True)
        args.sql.write_text(render_sql(articles), encoding="utf-8")
        print(f"\nWrote {args.sql}")

    if args.ts:
        args.ts.parent.mkdir(parents=True, exist_ok=True)
        args.ts.write_text(render_ts(articles), encoding="utf-8")
        print(f"Wrote {args.ts}")


if __name__ == "__main__":
    main()
