# Tolesh Design System — "Lagos Nocturne"

This document is the single source of truth for Tolesh's visual language. It is
implemented verbatim in [`web/styles/tokens.css`](web/styles/tokens.css) and
[`web/styles/app.css`](web/styles/app.css); components are documented in
[COMPONENT-LIBRARY.md](COMPONENT-LIBRARY.md).

## 1. The brief

Tolesh is a **personal** AI assistant, male-presenting, warm, intelligent and
culturally Nigerian. He is used in private, often for long stretches, on a
desktop. The interface's job is to make a conversation feel quiet, considered
and trustworthy — not to look like a SaaS dashboard, and not to perform
"AI-ness".

Design direction chosen after applying `frontend-design`, `ui-ux-pro-max` and
`impeccable`:

> **Lagos Nocturne** — warm near-black ink, burnished brass, a trace of adire
> indigo. An editorial serif for display type, a geometric humanist sans for
> everything else. Editorial restraint: one memorable element, everything else
> disciplined.

Why warm ink rather than blue-black: the brief is a person, not a cloud service.
Why brass rather than acid green or terracotta: `frontend-design` explicitly
lists "near-black + single acid-green/vermilion accent" and "warm cream +
terracotta" as the two most common generated-page tells. Brass is drawn from the
subject ( Nigerian brass-work, warm evening light ) rather than from a template.

## 2. Anti-template decisions

These are deliberate deviations from the defaults called out by the
`frontend-design` skill, applied during the interface audit:

| Default tell | Tolesh decision |
| --- | --- |
| One word in a headline set in italic / a different colour | Removed. `Good afternoon.` is one continuous display setting with no accented word |
| Tracked-out ALL-CAPS eyebrow above every heading | Removed the decorative "A QUIET PLACE TO THINK OUT LOUD" kicker; the topbar keeps `TOLESH / PERSONAL AI` only because it carries product identity |
| Identical rounded cards with one radius and one soft grey shadow | Panels use hairline dividers and flat tinted surfaces; radius varies by hierarchy (`--r-xs` → `--r-xl`) |
| Scattered fade-and-slide-up entrances on every card | One orchestrated GSAP timeline on load, plus motion that answers a user action |
| `→` appended to every button/link, monospace micro-labels | Used sparingly; copy names what the action does ("Send", "Add", "Delete all memory") |
| Gradient washes as decoration | The atmosphere gradient is a fixed ambient backdrop at 5–7% opacity, not a card treatment |

Copy follows the same discipline: plain verbs, sentence case, active voice,
specific labels, honest errors. Empty states are invitations ("No long-term
memory yet. Tolesh only stores what you explicitly add."), not mood.

## 3. Colour tokens

All colours are `oklch()` so lightness is perceptually consistent across hues.

### Surfaces

| Token | Value | Use |
| --- | --- | --- |
| `--ink-950` | `oklch(0.135 0.006 62)` | Page background |
| `--ink-900` | `oklch(0.165 0.008 62)` | Input / modal field background |
| `--ink-850` | `oklch(0.195 0.009 62)` | — |
| `--ink-800` | `oklch(0.225 0.010 62)` | Inline code background |
| `--ink-750` | `oklch(0.255 0.011 62)` | User avatar |
| `--ink-700` | `oklch(0.29 0.012 62)` | — |

### Lines

| Token | Value |
| --- | --- |
| `--line` | `oklch(0.335 0.012 62)` |
| `--line-soft` | `oklch(0.28 0.010 62 / 0.55)` |
| `--line-strong` | `oklch(0.42 0.014 70)` |

### Type

| Token | Value | Use |
| --- | --- | --- |
| `--ivory` | `oklch(0.955 0.012 82)` | Primary text |
| `--ivory-dim` | `oklch(0.88 0.014 80)` | Message bodies |
| `--muted` | `oklch(0.715 0.020 78)` | Secondary text |
| `--muted-dim` | `oklch(0.58 0.018 76)` | Tertiary / captions |

### Accents & semantic

| Token | Value | Use |
| --- | --- | --- |
| `--brass` | `oklch(0.795 0.132 82)` | Primary accent, focus ring, active state |
| `--brass-bright` / `--brass-deep` | `oklch(0.865 …)` / `oklch(0.62 …)` | Gradient endpoints for the brand mark and primary button |
| `--brass-08/14/24` | brass at 8% / 14% / 24% | Hover, selected, borders |
| `--terracotta` | `oklch(0.645 0.150 42)` | Listening state |
| `--indigo` / `--indigo-soft` | `oklch(0.335 0.070 265)` | Adire texture / ambient |
| `--ok` / `--warn` / `--danger` | green / amber / red | Status, risk tags, destructive actions |

`<html>` declares `color-scheme: dark` and
`<meta name="theme-color" content="#0D0C0A">` so native scrollbars, inputs and
the browser chrome agree with the theme.

## 4. Typography

| Role | Family | Notes |
| --- | --- | --- |
| Display | `"Bodoni Moda", "Didot", "Bodoni 72", Georgia, serif` | Headlines, brand mark, large numerals |
| Body | `"Jost", "Avenir Next", "Segoe UI", sans-serif` | All UI text and messages |
| Mono | `"SFMono-Regular", "JetBrains Mono", Consolas, monospace` | Tool names, command args, code |

Fonts are vendored locally under `web/assets/fonts/` (SIL OFL, from Google
Fonts) with `font-display: swap`, so the interface has no external font
dependency and no CDN `preconnect` requirement.

Type scale: `--fs-2xs` 11px → `--fs-3xl` 72px (see `tokens.css`). Body line
length is capped (`max-width: 52ch` on the intro copy, `68ch` on message
bubbles). Multi-line headings use `text-wrap: balance`; numeric readouts use
`font-variant-numeric: tabular-nums`.

## 5. Spacing, radii, elevation, motion

- **Spacing**: 4px base, `--s-1` (4px) → `--s-16` (64px).
- **Radii**: `--r-xs` 4px, `--r-sm` 8px, `--r-md` 12px, `--r-lg` 18px,
  `--r-xl` 26px, `--r-pill` 999px. Radius follows hierarchy, not a single value.
- **Elevation**: three shadows plus `--glow-brass` for focus/hover.
- **Motion**: `--ease-out cubic-bezier(0.22, 1, 0.36, 1)`,
  `--ease-in-out cubic-bezier(0.65, 0, 0.35, 1)`, `--dur-fast` 150ms,
  `--dur` 220ms, `--dur-slow` 420ms. Under `prefers-reduced-motion: reduce`
  all durations collapse to `0ms` at the token level. See
  [ANIMATION-GUIDELINES.md](ANIMATION-GUIDELINES.md).

## 6. Layout

```
┌──────┬───────────────────────────────────┬──────────────────┐
│ rail │  topbar (model selector, actions)  │  context panel   │
│ 84px │───────────────────────────────────│  336px           │
│      │  stream (conversation)             │  engine, approvals│
│      │                                   │  memory, providers│
│      │───────────────────────────────────│  usage, tools     │
│      │  composer                         │                  │
└──────┴───────────────────────────────────┴──────────────────┘
```

CSS Grid on `.app-shell`: `84px | minmax(0, 1fr) | 336px`, `100dvh`.
`minmax(0, 1fr)` plus `min-width: 0` on flex children allows long content to
truncate instead of blowing out the layout.

Breakpoints:

| Width | Behaviour |
| --- | --- |
| ≤ 1180px | Context narrows to 300px |
| ≤ 980px | Context becomes a fixed drawer; **starts collapsed** so it never covers the conversation on load |
| ≤ 680px | Rail becomes a 60px top bar; composer becomes a fixed bottom bar with `env(safe-area-inset-*)` padding |

Full-bleed chrome uses `viewport-fit=cover` and safe-area insets.
`overscroll-behavior: contain` on the drawer, modal and model menu prevents
scroll chaining.

## 7. Principles

1. **One bold element.** The brass brand mark / primary button carries the
   personality; everything around it stays quiet.
2. **Structure is information.** Hairline rules divide real sections; numbering
   and eyebrow labels appear only where they encode meaning.
3. **Motion answers actions.** One load sequence; hover/focus/reveal only where
   something changed. No endless decorative loops.
4. **Honest copy.** The UI states plainly when the backend is offline, when a
   provider needs a key, and that keys stay on the machine.
5. **Accessibility is a floor, not a feature.** Visible `:focus-visible` rings,
   semantic HTML before ARIA, `aria-hidden` on decorative icons, reduced-motion
   support, keyboard-operable listbox and dialogs. Full audit in
   [ACCESSIBILITY.md](ACCESSIBILITY.md).

## 8. How this system was produced

Skills applied: `ui-ux-pro-max` (design-system direction), `frontend-design`
(anti-template calibration), `impeccable` (craft floor / critique vocabulary),
`design-taste-frontend` (finish-gate mindset), `frontend-ui-engineering`
(component architecture), `web-design-guidelines` (compliance audit),
`fixing-accessibility` and `baseline-ui` (remediation). See
[SKILL-MATRIX.md](SKILL-MATRIX.md).

Deliverables are real files, not prose: `web/styles/tokens.css`,
`web/styles/app.css`, `web/styles/fonts.css`, `web/index.html`,
`web/js/components.js`, `web/js/motion.js`, plus two brand assets
(`web/assets/tolesh-mark.svg`, `web/assets/adire.svg`).
