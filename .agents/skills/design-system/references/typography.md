# Typography

## Font stack

| Token | Stack | Use |
| --- | --- | --- |
| `font-sans` | Helvetica → Arial → Noto Sans → sans-serif | All UI and running text |
| `font-display` | same stack (swap for a licensed display face later) | Signature style only |

Noto Sans covers non-Latin scripts. Do not introduce other fonts without updating this file and `src/web/styles.css`.

## Signature style

`font-display font-black uppercase tracking-wide`

Use for: page titles (H1), hero headlines, KPI numbers (no uppercase needed for digits), success/empty-state titles as brand moments.
Don't use for: body text, form labels, table content, buttons, card or dialog titles, long headlines (> ~6 words), anything below 20px.

## Type scale (mobile first → desktop)

| Level | Classes | Notes |
| --- | --- | --- |
| Display / H1 (page title) | `font-display font-black uppercase tracking-wide text-2xl md:text-3xl leading-none` | One per page |
| KPI number | `font-display font-black text-3xl md:text-4xl leading-none` | Proportional digits |
| H2 (section) | `font-sans text-lg font-bold md:text-xl` | Sentence case; also dialog/sheet titles (`text-lg font-bold`) |
| H3 (card title) | `font-sans text-base font-semibold` | Sentence case |
| Body | `font-sans text-sm md:text-base` | `leading-relaxed` for paragraphs |
| Label / button | `font-sans text-sm font-medium` / buttons `font-semibold` | |
| Caption / meta | `font-sans text-xs text-muted-foreground tracking-wide` | Timestamps, IDs |
| Overline (optional) | `font-sans text-xs font-bold uppercase tracking-widest text-muted-foreground` | Small section labels |

Rules:
- Sentence case everywhere except the signature style and overlines.
- Max 2 weights per component, max 3 sizes per card.
- Numbers in tables/lists: `tabular-nums`.
- Line length for text blocks ≤ ~70 characters (`max-w-prose`).
