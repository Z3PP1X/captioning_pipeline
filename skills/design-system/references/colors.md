# Colors

All values live in `src/styles.css` and are used via Tailwind token classes only.

## Brand

| Name | Value | Role | Tailwind |
| --- | --- | --- | --- |
| **mim26 (brand red)** | `#E33B17` | Brand color – large decorative areas, illustrations, brand moments without small text | `brand`, `mim26` (legacy alias) |
| **Primary** | `#D73816` | Accessible shade of mim26 – buttons, active states, focus ring, highlighted series, links | `primary` |
| **Ink** | `#1A1A1A` | Text, dark surfaces (nav rail, secondary buttons) | `foreground`, `secondary`, `sidebar`, `brand-ink` |
| **White** | `#FFFFFF` | Page/card surface, text on dark/primary | `background`, `card` |

## UI palette

| Token (CSS var → Tailwind) | Value | Use |
| --- | --- | --- |
| `--background` / `bg-background` | `#FFFFFF` | Page/card surface |
| `--canvas` / `bg-canvas` | `#E9EBEE` | App background behind cards |
| `--muted` / `bg-muted` | `#F4F5F6` | Subtle containers, table header, hover |
| `--muted-foreground` | `#65696F` | Secondary text (5.52:1 on white) |
| `--border` | `#D7DADC` | Decorative borders, dividers, cards |
| `--input` | `#868B90` | Form control borders (≥ 3:1, WCAG 1.4.11) |
| `--primary` / `--primary-foreground` | `#D73816` / `#FFFFFF` | Primary button, active tab/nav, progress |
| `--ring` | `#D73816` | Focus ring — always solid (`ring-ring`) |
| `--destructive` | `#D70015` | Errors, destructive actions |
| `--success` / `--warning` / `--info` | `#22C55E` / `#FFBC1F` / `#1658C7` | Status tints + icons (never as text color on white) |
| `--sidebar*` | Ink family | Dark navigation |
| `--chart-1…5` | Primary, Ink, Grey, Info, Warning | Charts: primary = highlighted series only |

Note: `--primary` (red) and `--destructive` (red) are close in hue. Destructive actions therefore always use the soft `destructive` button variant + a confirmation, and status uses icons — never rely on the red alone.

## Contrast matrix (WCAG 2.x, computed)

| Foreground ↓ / Background → | White | Ink | Primary | Brand | Canvas | Muted |
| --- | --- | --- | --- | --- | --- | --- |
| **White** | – | 17.40 ✔ | 4.70 ✔ | 4.28 large only | 1.19 ✘ | 1.09 ✘ |
| **Ink** | 17.40 ✔ | – | 3.70 ✘ | 4.06 large only | 14.57 ✔ | 15.94 ✔ |
| **Primary** | 4.70 ✔ | 3.70 ✘ | – | 1.10 ✘ | 3.94 large only | 4.31 large only |
| **Brand (mim26)** | 4.28 large only | 4.06 large only | 1.10 ✘ | – | 3.59 large only | 3.92 large only |
| **Grey `#65696F`** | 5.52 ✔ | 3.15 ✘ | 1.17 ✘ | 1.29 ✘ | 4.62 ✔ | 5.06 ✔ |

✔ = AA for normal text (≥ 4.5:1). "large only" = AA only for text ≥ 24px or bold ≥ 18.66px and for icons/UI boundaries (≥ 3:1).

Consequences:
- Buttons/surfaces with text: `bg-primary text-primary-foreground` (white). Never ink on primary, never small text on `bg-brand`.
- Primary text links on white are fine (4.70:1); on `bg-canvas` only bold/large or underlined with ink.
- Muted grey on ink fails — on dark surfaces use white / `sidebar-foreground` with opacity ≥ 70%.

## Do / Don't

- ✔ Primary for the single most important action, active state, focus, one highlighted data series.
- ✔ Large white/ink areas; red as a small, confident accent.
- ✘ Red backgrounds for large areas in product UI (hero/brand banners only, then with large text).
- ✘ Gradients, off-palette colors, pure `#000`, raw Tailwind palette classes, hex in templates.
