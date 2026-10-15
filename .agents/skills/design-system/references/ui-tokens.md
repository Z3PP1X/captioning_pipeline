# UI tokens & component styling

## Spacing scale → Tailwind

| Name | px | Tailwind |
| --- | --- | --- |
| 5xs / 4xs / 3xs | 2 / 4 / 8 | `0.5` / `1` / `2` |
| 2xs / xs / s | 12 / 16 / 20 | `3` / `4` / `5` |
| m / l / xl | 24 / 32 / 40 | `6` / `8` / `10` |
| 2xl / 3xl | 48 / 64 | `12` / `16` |

Page padding: `p-4` mobile, `md:p-6`, `lg:p-8`; page wrapper `mx-auto flex w-full max-w-screen-xl flex-col gap-6`. Stack gaps: `gap-2` (tight), `gap-4` (default), `gap-6` (sections). Avoid arbitrary pixel values when a scale step exists.

## Radius

`--radius` = 12px. Tailwind: `rounded-sm` ≈ 7px, `rounded-md` ≈ 10px, `rounded-lg` 12px, `rounded-xl` ≈ 17px, `rounded-2xl` ≈ 22px. Max 24px except pills (`rounded-full`).

| Element | Radius |
| --- | --- |
| Button, input, select | helm defaults (`rounded-md` / `rounded-lg`) |
| Chip / badge / filter pill | `rounded-full` |
| Card, sheet top edge, dialog | `rounded-xl` |
| Image thumbnails | `rounded-md` (small), `rounded-lg` (large) |

## Elevation

`shadow-xs` (cards on canvas), `shadow-sm` (hover/raised), `shadow-lg` only for overlays (popover, sheet, dialog — helm defaults). Cards: border **or** shadow, not both heavy.

## Components

| Component | Style |
| --- | --- |
| Primary button | `hlmBtn` default (primary red, white semibold label). One per view. Full width on mobile for the main CTA (`w-full sm:w-auto`). |
| Secondary button | `variant="outline"` (white + border) or `variant="secondary"` (ink). |
| Tertiary | `variant="ghost"` / `variant="link"` (ink, underlined). |
| Destructive | `variant="destructive"` (soft red) + confirm dialog. |
| Card | `hlmCard`, white, `rounded-xl`, `shadow-xs`, padding `p-4 md:p-6`. |
| Input | helm input, 44px mobile / 36px desktop, `border-input`, solid focus ring. |
| Badge | `hlmBadge variant="success" \| "warning" \| "danger" \| "muted"` + lucide icon (`data-icon="inline-start"`). Never hand-copy tint classes. |
| Table | helm table; header `bg-muted text-muted-foreground text-xs font-medium`; rows ≥ 56px on mobile. |
| Icons | Lucide via `@ng-icons/lucide`, 16px inline, 20px in nav/buttons; icon color follows text color. |

## Motion

150–200ms ease transitions (`transition-colors`, `duration-150`); `motion-safe:` for anything that moves; `tw-animate-css` for enter/exit. A global `prefers-reduced-motion` rule in `src/web/styles.css` neutralises animations/transitions.
