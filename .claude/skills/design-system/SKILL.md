---
name: design-system
description: Master design system for this project (colors, typography, UI tokens, mobile-first layout, accessibility). Use whenever designing, building or reviewing any UI — choosing colors, fonts, font sizes, headings, spacing, radii, shadows, icons, button/badge/card styling, page layouts or responsive behaviour. Works together with the `styling` skill (technical stack rules: Spartan + Tailwind only).
---

# Design system (master)

Single source of truth for **what** the UI looks like. The `styling` skill defines **how** it is built (Spartan helm/brain + Tailwind v4, tokens in `src/styles.css`). When both apply, follow both; on conflict, visual rules here win for visuals, `styling` wins for implementation technique.

## Details

| Topic | Reference |
| --- | --- |
| Brand + UI colors, token mapping, contrast matrix, do/don't | [references/colors.md](references/colors.md) |
| Font stack, signature style, type scale | [references/typography.md](references/typography.md) |
| Spacing, radius, shadow, icon, component styling | [references/ui-tokens.md](references/ui-tokens.md) |
| Mobile-first layout, page patterns, touch & accessibility | [references/layout.md](references/layout.md) |

## The 12 rules (read these even if you read nothing else)

1. **One brand accent.** The brand red (mim26) appears as `bg-primary` / `text-primary` (`#D73816`, an accessible shade of mim26 `#E33B17`) — for the primary action, active state, focus ring, key highlights. Max **one** primary action per view; no decorative use.
2. **Ink, not black.** Text and dark surfaces use `#1A1A1A` (`text-foreground`, `bg-secondary`, `bg-sidebar`), never `#000`.
3. **Brand hex only via tokens.** `bg-primary` for text-bearing surfaces (4.70:1 with white); `bg-brand` / `bg-mim26` only for large decorative brand areas without small text (4.28:1).
4. **Semantic ≠ brand.** Status uses `success`, `warning`, `info`, `destructive` — always icon + text, never color alone. Badges: `variant="success" | "warning" | "danger" | "muted"`.
5. **Text on primary is white** (`text-primary-foreground`). Never ink on primary (3.70:1 fails).
6. **Signature type** = `font-display font-black uppercase tracking-wide` for page titles (H1), KPI numbers and success titles only — not for body, labels, tables, card/dialog titles.
7. **Everything else** = `font-sans` regular / medium / bold, sentence case. Font stack: Helvetica → Arial → Noto Sans (non-Latin).
8. **Light mode first.** The `.dark` token block exists for Spartan consistency.
9. **Radii from the scale:** inputs/buttons `rounded-md…rounded-lg`, cards `rounded-xl`, max 24px; pills `rounded-full` for chips/badges. No square buttons.
10. **Lean surfaces:** white cards on `bg-canvas` with `border` or `shadow-xs`/`shadow-sm` — never both heavy. No gradients, no decorative color blocks.
11. **Mobile first:** design at 360px, enhance with `sm/md/lg`. Touch targets ≥ 44px below `md`, primary actions full width on mobile, bottom sheets over centered modals on phones, safe areas respected.
12. **Only tokens.** Never hardcode hex/rgb or raw palette classes (`gray-*`, `red-*`, `white`, `black`) in templates — new colors go into `src/styles.css` first and are documented in colors.md.

## Review checklist

- [ ] Only one primary (brand red) action per screen; no decorative red.
- [ ] White text on primary; no small text on `bg-brand`; primary text on canvas only large/bold (3.94:1).
- [ ] Display style only for H1 / KPI / success titles, uppercase.
- [ ] Status = semantic badge variant + icon + text.
- [ ] 360px layout works; touch targets ≥ 44px; focus ring visible and solid (`ring-ring`, never `/50`).
- [ ] Form controls use `border-input` (≥ 3:1).
- [ ] No hex values / raw palette classes in templates; no new CSS files (see `styling`).

## Known legacy to migrate

Existing templates predate this system (e.g. `header-component.html` uses `bg-[#a6c1ee]`, `text-gray-500`, `hover:bg-red-400`, an external logo URL; components have `.css` files). Migrate them to tokens + Spartan primitives when touching them.
