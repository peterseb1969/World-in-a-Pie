# WIP App UI Guidance — v1

**Status:** Canonical (CASE-302 propagated). Drafted in `FR-YAC/papers/ui-guidance.md`; this is the gene-pool authoritative copy that `create-app-project.sh` propagates into new app trees.
**Authority:** This file is the **shared visual anchor** for WIP apps. It does not dictate every pixel — spec §12.7 still trusts each APP-YAC on UI craft — but it provides Tailwind tokens, component shapes, and accessibility floors so different APP-YAC sessions don't drift into incompatible visual idioms.
**Scope:** v1 is deliberately thin. One page of concrete recommendations beats five pages of abstract design philosophy.

**Bias of this document:** *prescribe Tailwind tokens, not design talk.* Every section names class strings you can paste; nothing tells you to "establish a clear hierarchy" without saying which classes do that.

---

## 1. Color palette

The four sibling apps (WIP-Constellations, WIP-ClinTrial, WIP-DnD, WIP-AA) converged on the same palette. v1 makes that convergence official.

### Base tokens (extend `tailwind.config.{js,ts}` `theme.extend.colors`)

```ts
colors: {
  primary: {
    DEFAULT: '#2B579A',       // Microsoft-blue — used for primary actions, focus rings, link accents
    light:   '#5B9BD5',       // Hover / softer emphasis
    dark:    '#1E3F6F',       // Pressed state / strong contrast (optional — used by WIP-AA)
  },
  accent:    '#ED7D31',       // Orange — used sparingly for CTAs, highlights, hot moments
  success:   '#2E8B57',       // Sea green — checkmarks, "saved", health-OK
  danger:    '#DC3545',       // Bootstrap red — destructive actions, error states
  surface:   '#FFFFFF',       // Card / modal / dialog background
  background:'#F8FAFC',       // Page background (slate-50-ish; lighter than surface)
  text: {
    DEFAULT: '#333333',       // Body text — high contrast on surface
    muted:   '#999999',       // Captions, secondary labels, placeholders
  },
}
```

### Recommended usage (named, not invented)

| Use | Class anchor |
|---|---|
| Primary button | `bg-primary text-white hover:bg-primary-light` |
| Secondary button | `border border-primary text-primary hover:bg-primary/5` |
| Destructive button | `bg-danger text-white hover:bg-danger/90` |
| Tertiary / ghost | `text-primary hover:bg-primary/5` |
| Page background | `bg-background` (the slate-50 token, not `bg-surface`) |
| Card / panel surface | `bg-surface` (white) |
| Body text | `text-text` |
| Muted text | `text-text-muted` |
| Link | `text-primary hover:underline` |
| Focus ring | `focus:outline-none focus:ring-2 focus:ring-primary/40` |

### Tinted callouts (the most-used pattern in the four apps)

For inline callouts (primary info banner, error message, success notice), use a `bg-{color}/5` + `border-{color}/20` + `text-{color}` icon-and-heading triplet:

```tsx
// Primary info / filter bar
<div className="rounded-lg border border-primary/20 bg-primary/5 p-4">
  <h3 className="font-medium text-primary">Heading</h3>
  <p className="mt-1 text-sm text-text-muted">Body text...</p>
</div>

// Error
<div className="rounded-lg border border-danger/20 bg-danger/5 p-4">
  <h3 className="font-medium text-danger">Error heading</h3>
  ...
</div>

// Success uses the same shape with `success` substituted.
```

This pattern is in every sibling app. Adopt it; don't invent a new tinted-callout shape.

### Do not introduce

- New named colors without extending `tailwind.config.js`. (Inline hex codes are forbidden — they break the convention.)
- Bootstrap's full color set. Only `danger`'s shade matches Bootstrap; the rest of the palette is constellation-specific.
- Dark mode in v1. The four apps are light-mode-only. Dark mode is a future cross-app project.

---

## 2. Typography

### Family

```ts
// tailwind.config.{js,ts}
fontFamily: {
  sans: ['Inter', 'system-ui', 'sans-serif'],
}
```

Inter is the constellation default. Load via `<link>` in `index.html`:

```html
<link href="https://rsms.me/inter/inter.css" rel="stylesheet">
```

Or, for the offline-friendly setup that WIP-Constellations uses, vendor the font via npm and import locally.

### Hierarchy (concrete Tailwind classes)

| Use | Classes |
|---|---|
| Page title (h1) | `text-2xl font-semibold tracking-tight text-text` |
| Section title (h2) | `text-xl font-semibold text-text` |
| Subsection title (h3) | `text-lg font-semibold text-text` |
| Card title | `text-lg font-semibold text-text` (matches h3 — same weight, smaller leading) |
| Body | `text-base text-text` |
| Body small | `text-sm text-text` |
| Caption / muted | `text-sm text-text-muted` |
| Label (form / chip) | `text-xs font-medium text-text-muted uppercase tracking-wide` |
| Mono / code inline | `font-mono text-sm bg-background rounded px-1.5 py-0.5` |
| Mono block | `font-mono text-sm bg-background rounded p-3 overflow-x-auto` |

### Avoid `text-3xl+` without a reason

CASE-302 was filed *specifically* because an unanchored `text-3xl` doc title looked wrong. Default to `text-2xl` for page titles; jump to `text-3xl` only for marketing-shape pages (landing screens, intro modals — none in current apps).

---

## 3. Spacing & density

### Page chrome

Always wrap page content in a centered max-width container. The constellation default is `max-w-6xl` (~72rem):

```tsx
<main className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">
  ...
</main>
```

For docs-shaped pages (single article, long-read), `max-w-4xl` is acceptable. For full-bleed dashboards (RC-Console), drop the max-width entirely and use `mx-auto px-4`.

### Vertical rhythm

- **Default gap between siblings**: `space-y-4` (1rem). Tighter blocks (form fields, list items) use `space-y-2`. Looser sections (cards in a grid) use `gap-6`.
- **Card padding**: `p-4` (default) or `p-6` (loose). Don't go below `p-3`; touch targets get cramped.
- **Modal padding**: header `px-5 py-3`, body `p-5`, footer `px-5 py-3`.

### Border radius

- Default: `rounded-lg` (0.5rem). Cards, modals, callouts.
- Compact: `rounded-md` (0.375rem). Buttons, badges, chips.
- Full: `rounded-full`. Pills, avatars, status dots.

Don't use `rounded-xl` / `rounded-2xl` without intent — softens the visual register and looks consumer-y rather than enterprise-y.

---

## 4. Component shapes

These are the de facto component patterns across the four apps. New apps adopt these; new component types (anything not listed) are a chance to file a small UI case proposing the shape.

### Card

```tsx
<div className="rounded-lg border border-gray-200 bg-surface p-4 shadow-sm">
  <h3 className="text-lg font-semibold text-text">Card title</h3>
  <p className="mt-2 text-sm text-text-muted">Card description...</p>
</div>
```

### Primary button

```tsx
<button
  type="button"
  className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-light focus:outline-none focus:ring-2 focus:ring-primary/40 disabled:cursor-not-allowed disabled:opacity-50"
>
  Action
</button>
```

### Secondary / ghost button

```tsx
<button className="rounded-md border border-primary px-4 py-2 text-sm font-medium text-primary hover:bg-primary/5 focus:outline-none focus:ring-2 focus:ring-primary/40">
  Cancel
</button>
```

### Modal shell

```tsx
<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
  <div className="relative flex max-h-[90vh] w-full max-w-2xl flex-col rounded-lg bg-surface shadow-xl">
    {/* Header */}
    <div className="flex items-center justify-between border-b px-5 py-3">
      <h2 className="text-lg font-semibold text-text">Modal title</h2>
      <button className="text-text-muted hover:text-text" aria-label="Close">
        <X className="h-4 w-4" />
      </button>
    </div>
    {/* Body */}
    <div className="flex-1 overflow-y-auto p-5">
      ...
    </div>
    {/* Footer */}
    <div className="flex items-center justify-end gap-2 border-t px-5 py-3">
      <SecondaryButton>Cancel</SecondaryButton>
      <PrimaryButton>Confirm</PrimaryButton>
    </div>
  </div>
</div>
```

### Badge

```tsx
<span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
  Active
</span>
```

For a non-tinted neutral badge, use `bg-gray-100 text-text-muted`. For status colors, swap `primary` for `success` / `danger` / `accent`.

### Facet pill / chip (selectable)

```tsx
<button
  className={cn(
    'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium',
    isSelected
      ? 'border-primary bg-primary/10 text-primary'
      : 'border-gray-200 bg-surface text-text-muted hover:bg-gray-50'
  )}
>
  {isSelected && <Check className="h-3 w-3" />}
  {label}
</button>
```

### List row

```tsx
<div className="flex items-center justify-between rounded-md border border-gray-200 bg-surface p-3 hover:bg-background">
  <div className="flex flex-col gap-1">
    <span className="text-sm font-medium text-text">Primary text</span>
    <span className="text-xs text-text-muted">Secondary text</span>
  </div>
  <span className="text-xs text-text-muted">{timestamp}</span>
</div>
```

### Page header

```tsx
<header className="mb-6 flex items-start justify-between">
  <div>
    <h1 className="text-2xl font-semibold tracking-tight text-text">Page title</h1>
    <p className="mt-1 text-sm text-text-muted">Optional one-line description.</p>
  </div>
  <div className="flex gap-2">
    <SecondaryButton>Cancel</SecondaryButton>
    <PrimaryButton>Save</PrimaryButton>
  </div>
</header>
```

---

## 5. Icons

`lucide-react` is the constellation default. Standard sizes:

| Use | Class |
|---|---|
| Inline with text | `h-4 w-4` |
| Button icon | `h-4 w-4` (with `gap-2` from the label) |
| Standalone toolbar icon | `h-5 w-5` |
| Empty-state large icon | `h-8 w-8` or `h-10 w-10` |

Icon color follows the surrounding text color. Don't manually color icons unless they're intentionally not text — e.g., `text-success` on a status dot, `text-danger` on a warning glyph.

---

## 6. Accessibility floor

These are non-negotiable; every component must meet them.

1. **Focus rings.** All interactive elements (buttons, inputs, anchors, custom-tabbable divs with `tabIndex`) must show a visible focus ring on `:focus-visible`. The convention is `focus:outline-none focus:ring-2 focus:ring-primary/40`. Do NOT remove focus styles without replacing them.
2. **ARIA labels for icon-only buttons.** Any `<button>` whose visible content is an icon (close-X, copy-clipboard, expand-chevron) needs `aria-label="..."`.
3. **Modals trap focus.** Escape closes; tab cycles within the modal; clicking the backdrop closes (unless the modal is destructive — then require explicit cancel). The modal shell above is the template.
4. **Color is never the only signal.** A red border alone doesn't communicate "error" to colorblind users — pair it with an icon (`AlertTriangle`) and a text label.
5. **Keyboard navigation.** Lists are arrow-key-traversable where it makes sense; primary actions have explicit shortcuts (`Cmd+S` for save, `Esc` to cancel).
6. **Touch targets.** Minimum 32×32 px (the size of an `h-8 w-8` icon button with `p-1`). Don't go smaller in dense lists; users with motor variations can't hit a 16-px target reliably.

---

## 7. Empty states

Most pages have an "I have nothing to show you" state. The convention:

```tsx
<div className="flex flex-col items-center justify-center py-12 text-center">
  <Icon className="h-10 w-10 text-text-muted" />
  <p className="mt-3 text-base font-medium text-text">No items yet</p>
  <p className="mt-1 text-sm text-text-muted">
    {hint || 'Once content is added, it will appear here.'}
  </p>
  {action && <div className="mt-4">{action}</div>}
</div>
```

For dormant-app empty states (the KB-app-with-no-doc case from spec Q1), drop the icon entirely and use a single sentence — *"a YAC needs to write a doc before the UI is activated."* The minimal form prevents users from mistaking dormancy for breakage.

---

## 9. Brand attribution — `<WipFooter>`

Every WIP app ends with a small, muted "Built on WIP" footer. The constellation's visible identity disappeared when the Vue Console was retired; `<WipFooter>` (shipped in `@wip/react@0.11+`) brings it back as a one-line drop-in.

### Canonical use

```tsx
import { WipFooter } from '@wip/react'

export default function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <Layout>
        <Routes>...</Routes>
        <WipFooter />
      </Layout>
    </BrowserRouter>
  )
}
```

The component is purely presentational — no hooks, no MCP, no state. Drop it as a leaf at the end of your root layout.

### Rendered shape (reference — for when you can't import yet)

```tsx
<footer className="mt-12 border-t border-gray-200 py-4">
  <div className="mx-auto flex max-w-6xl items-center justify-center gap-2 px-4 text-xs text-text-muted">
    <img src="..." alt="WIP" className="h-4 w-auto opacity-70" />
    <span>Built on WIP</span>
  </div>
</footer>
```

- **Position**: end-of-page, NOT sticky. Pushes off-screen on scroll.
- **Color register**: `text-xs text-text-muted` — subtle, ignorable when not needed.
- **Logo treatment**: `h-4 w-auto opacity-70` (~16px tall, softened blue).
- **Top border**: `border-t border-gray-200` separates from page content above.

### Props (all optional)

| Prop | Effect |
|---|---|
| `appName="<name>"` | Renders as "ClinTrial · Built on WIP" — for apps that want their name alongside |
| `className="<extra>"` | Layout override (margin, alignment); color/typography stay locked to canonical tokens |
| `variant="full"` | Reserved for v1.5 (would add app version + WIP version + a link to project docs); not yet implemented |

### Do NOT

- Don't make it sticky. The footer is end-of-content, not chrome.
- Don't override the color register (`text-text-muted` muted is the entire point — saturated brand colors here read as marketing-shape).
- Don't omit it. Every WIP app should have it, even single-page admin tools.
- Don't roll your own — use `<WipFooter>`. If your needs aren't met, file a case to extend the component, not to bypass it.

### Tracking
- **CASE-302**: this section's parent (UI design guidance gap).
- **CASE-308**: the implementable spec for `<WipFooter>` itself, library bump, per-app rollout coordination. Filed 2026-05-08.

---

## 10. What this guidance does NOT cover

- **Brand identity / logos.** No app needs more than a wordmark in v1.
- **Marketing pages.** No app has one in v1.
- **Charts & data viz.** No standard yet — when the first app needs charts (likely WIP-Constellations or a future analytics surface), file a case proposing the chart library + token mapping.
- **Animations.** Tailwind's defaults (`transition`, `duration-150`, `ease-in-out`) are enough. No `framer-motion` in v1; revisit when a UI demands choreographed motion.
- **Per-app brand variants.** Each APP-YAC can extend the base palette (e.g., add a domain-specific color) by extending `theme.extend.colors` further. The base must exist before extensions are coherent.

---

## Changelog

- **2026-05-08 (v1.0)**: First version. Closes the visual half of CASE-302. Tailwind tokens extracted from the four sibling apps' tailwind configs; component shapes from clintrial-explorer's `src/components/`. Authoritative for new apps from APP-KB onward; existing apps grandfathered.
- **2026-05-08 (v1.0.1)**: Added §9 (brand attribution / `<WipFooter>`). Tracks CASE-308 — the component itself ships in `@wip/react@0.11+`; this section is the canonical guidance for use. Section numbers shifted (old §9 *"What this guidance does NOT cover"* is now §10).
