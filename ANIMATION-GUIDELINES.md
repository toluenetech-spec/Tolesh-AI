# Tolesh Animation Guidelines

> Motion explains a state change or answers a user action. It is never decoration,
> never an endless loop, and never a substitute for feedback.

This document describes the animation that is actually implemented in
[`web/js/motion.js`](web/js/motion.js), [`web/styles/app.css`](web/styles/app.css)
and [`web/styles/tokens.css`](web/styles/tokens.css). It follows the
`design-motion-principles` and `fixing-motion-performance` skills and the
animation rules in the Vercel Web Interface Guidelines.

## 1. Principles

| Principle | How Tolesh honours it |
| --- | --- |
| One orchestrated entrance | A single `gsap.timeline()` staggers rail → topbar → empty state → panels → composer. No scattered per-card fade-ins on load |
| Motion answers an action | Message arrival, sending state, dialog open, drawer toggle and voice listening all animate; nothing animates on its own indefinitely |
| Fast, not showy | 150–420ms via `--dur-fast` / `--dur` / `--dur-slow`; `--ease-out` for entrances |
| Respect the user | Three independent reduced-motion guards (§6) |
| Compositor-only | Only `transform` and `opacity` are animated; no layout-triggering properties |
| Honest feedback | A busy send button shows a spinner; a streaming bubble shows a caret. No fake waveform when the Speech API is unavailable |

## 2. Three layers

| Layer | File | Used for |
| --- | --- | --- |
| CSS transitions / keyframes | `app.css` | Hover, focus, drawer, messages, toasts, modals, caret, busy spinner |
| GSAP timeline | `motion.js` | The single page entrance and the user-triggered listening pulse |
| Web Animations API | `motion.js` | Fallback when `web/vendor/gsap.min.js` fails to load |

GSAP is vendored locally, so motion never depends on a CDN. It is loaded as a
classic script before the `app.js` module; the WAAPI fallback keeps the UI alive
if it is missing.

## 3. The entrance timeline

Verbatim from `initMotion()`:

```js
const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
tl.from('.rail', { x: -18, opacity: 0, duration: 0.5 })
  .from('.topbar > *', { y: -10, opacity: 0, stagger: 0.07, duration: 0.42 }, '-=0.26')
  .from('.empty-state > *', { y: 16, opacity: 0, stagger: 0.08, duration: 0.52 }, '-=0.2')
  .from('.context .panel-section', { x: 14, opacity: 0, stagger: 0.055, duration: 0.42 }, '-=0.34')
  .from('.composer', { y: 14, opacity: 0, duration: 0.46 }, '-=0.3');

gsap.fromTo('.orb-ring-1',
  { scale: 0.88, opacity: 0 },
  { scale: 1.08, opacity: 0.3, duration: 1.6, ease: 'sine.out' });
```

`gsap.matchMedia()` scopes the whole timeline to
`(prefers-reduced-motion: no-preference)`. The halo was deliberately changed
from an infinite autoplay loop to **one settling tween** — the Web Interface
Guidelines require a pause/stop control for autoplay motion longer than five
seconds, and a pulse that never stops is not a control.

## 4. User-triggered motion

| Trigger | Animation | Stops when |
| --- | --- | --- |
| Voice session starts | `setOrbListening(true)` — `orb-ring-2` pulse `repeat: -1`, caption and `aria-label` change | `setOrbListening(false)` calls `gsap.killTweensOf('.orb-ring-2')` and resets opacity |
| Streaming reply | `.caret` blink (`steps(2, start)`), message `msg-in` | `finishMessage()` removes the caret |
| Submit while streaming | `.send-btn.is-busy` spinner (`send-spin`) | Response completes |
| Dialog / toast | `modal-in`, `toast-in`, `fade-in` | One-shot, `both` fill |
| New context card | `animateIn(el)` — 320ms reveal | One-shot |
| Drawer toggle | `.context` opacity/transform transition | Reaches resting state |

The listening pulse is the **only** infinite animation in the app. It is
started by an explicit user action and killed when that action ends.

## 5. Performance rules

- Animate `transform` and `opacity` only. `msg-in`, `toast-in`, `modal-in`,
  `send-spin`, `caret-blink` and every GSAP tween obey this.
- **No `transition: all`.** Every transition names its properties, e.g.
  `transition: color var(--dur-fast), border-color var(--dur-fast),
  background-color var(--dur-fast)`. This was one of the concrete Vercel
  guideline findings that was fixed.
- `html` sets `overscroll-behavior: none`; `.model-menu`, `.context` and
  `.modal-backdrop` set `overscroll-behavior: contain` so scrolling a panel
  does not scroll the page behind it.
- No `will-change` is sprayed across the tree; GSAP manages transforms itself.
- `color-scheme: dark`, `-webkit-text-size-adjust: 100%`,
  `-webkit-tap-highlight-color: transparent` and `touch-action: manipulation`
  keep interaction responsive without 300ms tap delay.
- `env(safe-area-inset-*)` padding on mobile full-bleed chrome avoids layout
  shifts on notched devices.
- Scroll uses `scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth' })`.

## 6. Reduced motion — three independent guards

1. **JavaScript**: `initMotion()` and `animateIn()` return immediately when
   `prefersReducedMotion()` is true; `setOrbListening()` skips the pulse and
   kills existing tweens.
2. **Tokens**: `tokens.css` collapses `--dur-fast`, `--dur` and `--dur-slow`
   to `0ms` under `@media (prefers-reduced-motion: reduce)`.
3. **CSS**: `app.css` forces
   `animation-duration: 0.001ms !important`,
   `animation-iteration-count: 1 !important`,
   `transition-duration: 0.001ms !important` and `scroll-behavior: auto`.

Because the guards are independent, a user who reduces motion gets a static
interface even if one layer fails.

## 7. Token reference

| Token | Value | Use |
| --- | --- | --- |
| `--dur-fast` | 150ms | Hover, focus, small state changes |
| `--dur` | 220ms | Transitions, toast, modal |
| `--dur-slow` | 420ms | Message entrance |
| `--ease-out` | `cubic-bezier(0.22, 1, 0.36, 1)` | Entrances |
| `--ease-in-out` | `cubic-bezier(0.65, 0, 0.35, 1)` | Symmetric changes |

All three durations become `0ms` under reduced motion. GSAP uses fixed
durations (0.32–1.6s) but is entirely disabled by guard 1.

## 8. Audit checklist

| Guideline | Status |
| --- | --- |
| No autoplay motion > 5s without pause control | Fixed — infinite halo removed; only user-triggered pulse remains |
| `prefers-reduced-motion` honoured | Fixed — three guards |
| Animate compositor properties only | Fixed |
| No `transition: all` | Fixed |
| Scoped scroll containers | Fixed |
| Focus does not move because of animation | Fixed — no layout-shifting entrance on focusable elements after load |
| Loading/busy state communicated | Fixed — `.send-btn.is-busy` + `.stream-status` |
| Motion does not block input | Fixed — no blocking overlay animation |

## 9. Verification

Performed: Chromium/Playwright regression captured desktop (1440×900) and
mobile (390×844) screenshots, streamed a real chat through `/api/chat`, and
collected `console.error` / `pageerror` (zero JavaScript errors). A static
check confirms no `transition: all` remains in `app.css`.

Not yet automated (honest gaps): frame-rate/performance profiling under
sustained streaming, and an explicit browser run with
`prefers-reduced-motion: reduce` emulation. The code paths exist and are
reviewed, but no automated test asserts them yet. See [TESTING.md](TESTING.md).
