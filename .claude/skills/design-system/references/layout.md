# Layout, mobile first & accessibility

## Breakpoints (Tailwind defaults)

Design at **360px** first. `sm` 640 · `md` 768 · `lg` 1024 · `xl` 1280. Max content width `max-w-screen-xl`, centered.

## App shell (recommended pattern)

- **Header** (sticky, `h-14 md:h-16`, white, bottom border): menu button + logo left, optional search center (icon → full-screen overlay on mobile, bar from `md`), actions right.
- **Navigation**: below `lg` a drawer (`hlm-sheet` side="left"); from `lg` a permanent (optionally collapsible) rail. One active item at a time, `aria-current="page"`.
- **Main**: `main#main-content` with a skip link; pages provide padding and a page header (H1 in signature style).

## Page patterns

| Pattern | Mobile | ≥ md |
| --- | --- | --- |
| List / overview | Stacked cards or a table with 2–3 priority columns; filters in a bottom sheet; primary CTA full width | Full table/grid, inline filters |
| Detail | Stacked: media → key facts → secondary content → sticky bottom action bar | 2 columns |
| Form / wizard | One step per screen, progress "Schritt x von n", sticky bottom Back/Next | Inline actions |
| Content page (FAQ, Impressum) | Single column, `max-w-prose` text | Centered column, optional side nav |
| Modal | Bottom sheet (`side="bottom"`, rounded top, safe-area padding) | Centered dialog `max-w-lg` |

## Touch & interaction

- Touch targets ≥ 44×44px (helm buttons/inputs are `h-11` below `md`).
- Primary actions reachable by thumb: bottom of screen / sticky action bar on mobile.
- Safe areas: `pb-[env(safe-area-inset-bottom)]` on bottom bars/sheets; viewport meta with `viewport-fit=cover`.
- No hover-only affordances; everything reachable by tap and keyboard.

## Accessibility

- Contrast per colors.md matrix (AA). Status never by color alone (icon + text).
- Every control labelled (`hlmLabel` / `hlm-field`), icon buttons with `sr-only` text.
- Visible, solid focus ring (`--ring`), never removed.
- Landmarks: `header`, labelled `nav`, `main#main-content`, skip link; one `h1` per page; focus moves to `main` after route changes.
- Respect `prefers-reduced-motion` (global rule + `motion-safe:`).
- `lang` attribute matches the UI language; translated copy via the translation service.
