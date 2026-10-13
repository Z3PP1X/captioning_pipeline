---
name: styling
description: Style guide for this Angular project. Use whenever writing or reviewing component templates, styles, or anything involving UI/CSS — building a new component, adding a form control, styling a layout, choosing colors/spacing, or touching any .css/.html/.ts template. Enforces Spartan UI (helm/brain) components and Tailwind CSS v4 for all styling.
---

# Styling & UI style guide

This project is Angular 22 + Tailwind CSS v4 + Spartan NG (`@spartan-ng/brain` + generated helm components). Follow these rules for any UI or styling work.

**Visual rules** (colors, typography, spacing, mobile-first layout, contrast) live in the master design guide: [design-system](../design-system/SKILL.md). Read it together with this file — this file covers *how* to build, the design guide covers *what it must look like*.

## Core rules

1. **Never hand-roll a UI primitive that Spartan already provides.** Buttons, inputs, selects, dialogs, dropdowns, tooltips, tabs, checkboxes, switches, popovers, cards, badges, avatars, etc. must come from Spartan (`@spartan-ng/helm/*` components generated via the CLI, built on `@spartan-ng/brain`), not custom markup or a different component library.
2. **Never write plain CSS files or inline `style=` for layout/spacing/color.** Use Tailwind utility classes in the template. Component `.css` files should stay empty or contain only `@apply`-based rules that can't be expressed inline, or truly component-scoped concerns Tailwind can't reach (e.g. `:host` display).
3. **Never introduce another component/UI library** (Angular Material, PrimeNG, Bootstrap, ad-hoc npm UI kits, raw third-party CSS frameworks). Spartan + Tailwind is the only stack.
4. **Use the design tokens already defined in `src/styles.css`**, not hardcoded hex/oklch values. Reference semantic tokens via their Tailwind classes (`bg-background`, `text-foreground`, `bg-primary`, `text-primary-foreground`, `border-border`, `bg-muted`, `text-muted-foreground`, `bg-destructive`, etc.) so light/dark theming (`:root` / `:root.dark`) keeps working.
5. **Compose class lists with `cn()` / `hlm()`-style merging** (`tailwind-merge`, already a dependency) rather than string concatenation, to avoid conflicting Tailwind utilities.
6. **Forms use Angular Signal Forms** (`@angular/forms/signals`: `form()`, schema validators like `required`/`email`, `[formField]` on `hlmInput` / `hlm-checkbox`, `submit()` which marks all fields touched). No `ReactiveFormsModule` / `FormGroup` / `ngModel` in feature code. Spartan brain picks up the field state through Angular's `InteropNgControl`, so `hlm-field-error validator="…"` and the touched-aware `aria-invalid` work unchanged. Do not also set `required`/`disabled`/`value` attributes on a `[formField]` node (NG8022) – declare them in the schema.

## Adding a Spartan component

Components are not pre-vendored — generate them on demand into `src/app/shared/ui` (per `components.json`, `importAlias: "@spartan-ng/helm"`, `style: "vega"`):

```bash
npx ng g @spartan-ng/cli:ui <component-name> --directory=src/app/shared/ui
```

This scaffolds the component under `src/app/shared/ui/<name>` and wires up the `@spartan-ng/helm/<component-name>` import. Import it from there — do not copy component source by hand or reimplement it.

If a generator run fails or the CLI reports drift, run:

```bash
npx ng g @spartan-ng/cli:healthcheck
```

## Project adaptations of the generated primitives (keep them when regenerating)

The helm sources under `src/app/shared/ui` are owned code and carry design-system changes. If you regenerate a primitive, re-apply:
- Touch targets: button `default` `h-11 md:h-9`, `lg` `h-12 md:h-10`, `icon` `size-11 md:size-9`, `icon-lg` `size-12 md:size-10`; input / native-select / input-group `h-11 md:h-9`; select trigger `data-[size=default]:h-11 md:data-[size=default]:h-9`.
- Focus rings are solid: `ring-ring` (never `ring-ring/50`), `outline-ring`.
- `hlmInput` / `hlmTextarea` do **not** use `BrnInput` / `BrnTextarea` as host directive: they re-implement them (host directives `BrnFieldControl` + `BrnFieldControlDescribedBy`, `provideBrnLabelable(...)`, `id` / `forceInvalid` inputs, `data-invalid|touched|dirty|matches-spartan-invalid`) so `aria-invalid` has a single source, `forceInvalid() || BrnFieldControl.spartanInvalid()` (only after touch/submit). Reason: the brain directives bind `aria-invalid` to the raw `invalid()`; with `formControlName` the control resolves one tick late and that binding overrides any touched-aware one, so pristine required fields got `aria-invalid="true"`. Covered by `shared/ui/input/src/lib/hlm-input.spec.ts` (formControlName + `[formControl]`).
- Button `link` variant: `text-foreground underline underline-offset-4 hover:text-primary`.
- Badge variants `success | warning | danger | muted` (semantic tints, foreground text).
- No raw palette classes in primitives (`text-gray-*` → `text-muted-foreground`, overlay `bg-black/*` → `bg-foreground/*`).

## Tailwind conventions

- Tailwind v4 is configured via CSS (`@import 'tailwindcss/...'` + `@spartan-ng/brain/hlm-tailwind-preset.css`) in `src/styles.css` — there is no `tailwind.config.js` to edit for standard theme values. New design tokens go in the `:root` / `:root.dark` blocks in `src/styles.css` as CSS variables, following the existing naming pattern (`--<token>` plus `--<token>-foreground` where applicable).
- Prefer semantic tokens over raw Tailwind palette colors (`bg-primary` over `bg-black`, `text-muted-foreground` over `text-gray-500`) so the UI stays theme-consistent.
- Use `tw-animate-css` utilities for animation rather than hand-written `@keyframes`.
- Keep responsive/state variants (`sm:`, `dark:`, `hover:`, `data-[state=open]:`) inline in the template class list — that is the Spartan/Tailwind convention already used by helm components, don't move them into a stylesheet.

## When in doubt

If a needed UI pattern has no Spartan primitive, compose it from existing Spartan primitives + Tailwind utilities first. Only fall back to a fully custom element if genuinely nothing in Spartan fits, and keep its styling in Tailwind utility classes, not custom CSS.
