# Article content format

`insights.content` and the five narrative columns on `case_studies`
(`challenge`, `strategic_approach`, `solution`, `execution`, `results_summary`)
are `jsonb` arrays of blocks.

Blocks rather than HTML, for two reasons:

1. **Nothing user-authored is ever passed to `dangerouslySetInnerHTML`.** There
   is no sanitiser to get wrong because there is no HTML to sanitise.
2. **The existing design is preserved exactly.** Each block type maps to the
   specific typography the hand-built article pages already use, so a
   database-driven article is visually identical to the original.

The frontend validates every block at read time in
`src/app/types/content.ts` (`parseContentBlocks`). Malformed blocks are dropped
rather than rendered, so one bad entry cannot break a page.

## Block types

| Type | Fields | Renders as |
|---|---|---|
| `paragraph` | `text`, `variant?: "lead" \| "default"` | Body copy. `lead` is the larger opening paragraph. |
| `heading` | `level: 2 \| 3`, `text` | Section heading. |
| `image` | `url`, `alt`, `caption?` | Full-width figure. |
| `quote` | `text`, `attribution?` | Blockquote. |
| `pull_quote` | `text` | The left-rule emphasis treatment. |
| `bullet_list` | `items: string[]` | Unordered list. |
| `numbered_list` | `items: string[]` | Ordered list. |
| `divider` | — | Hairline rule. |
| `cta` | `label`, `href`, `description?` | Inline call-to-action button. |

## Example

```json
[
  {
    "type": "paragraph",
    "variant": "lead",
    "text": "Culture is no longer a layer on top of marketing. It is the strategy."
  },
  { "type": "divider" },
  { "type": "heading", "level": 2, "text": "1. Culture Is the New Distribution Channel" },
  {
    "type": "paragraph",
    "text": "Traditional distribution is expensive, controlled, and increasingly inefficient."
  },
  {
    "type": "pull_quote",
    "text": "If culture carries the message, distribution takes care of itself."
  },
  {
    "type": "image",
    "url": "https://<project>.supabase.co/storage/v1/object/public/media/2026/08/hero.webp",
    "alt": "Amapiano artists performing in Johannesburg",
    "caption": "Amapiano's global rise, 2021–2026."
  }
]
```

## Constraints

`insights.content` carries a check constraint requiring `jsonb_typeof(content) = 'array'`,
so a bare object or scalar can never be stored. Empty is the default (`'[]'`).
