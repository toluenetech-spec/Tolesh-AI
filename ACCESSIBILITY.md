# Tolesh Accessibility

> The interface should work for someone using only a keyboard, someone who has
> asked their OS to reduce motion, and someone whose browser lacks the Web
> Speech API. Accessibility here is a set of concrete code changes, not a claim.

## 1. Basis of the audit

- **Vercel Web Interface Guidelines** — the `web-interface-guidelines/command.md`
  was fetched live and applied rule by rule with `file:line` findings.
- **Installed skills**: `fixing-accessibility`, `baseline-ui`,
  `frontend-ui-engineering`, `improve-ui`, `design-taste-frontend`.
- **Verification**: Chromium/Playwright regression (desktop + mobile),
  keyboard interaction checks, and the new `tests/test_tolesh_server.py`
  contract tests. See [TESTING.md](TESTING.md).

## 2. Findings and fixes

| # | Rule | Location | Problem | Fix |
| --- | --- | --- | --- | --- |
| 1 | `transition: all` is a performance/animation smell | `web/styles/app.css` | Broad transitions on interactive elements | Every transition now names explicit properties |
| 2 | Declare `color-scheme` | `app.css` | Dark UI without `color-scheme: dark` → wrong form controls/scrollbars | Added on `html`, plus `-webkit-text-size-adjust: 100%` and `overscroll-behavior: none` |
| 3 | Decorative icons must be hidden | `web/index.html` | Inline SVG icons announced by AT | `aria-hidden="true"` on all decorative `<svg>`, `.atmosphere`, `.grain`, `.icon-sprite`, `.brand-mark`, `.orb-ring`, `.orb-level` |
| 4 | No fake tab semantics | `index.html` | `role="tablist"` / `role="tab"` with no tabpanel and no arrow-key handling | Replaced with ordinary nav buttons carrying `aria-current="true"` |
| 5 | Disclosure needs state | `index.html` | Context toggle had no `aria-expanded` | Added `aria-expanded="true"` and `aria-controls="contextPanel"` on `#contextToggle` |
| 6 | Form fields need names | `index.html` | Composer `<textarea>` had no `name` | Added `name="message"`, `autocomplete="off"`, `spellcheck="true"` |
| 7 | Provide a skip link | `index.html` | No way to bypass the rail | `.skip-link` → `#mainView`; `<main>` given `tabindex="-1"` |
| 8 | Dialogs need focus management | `web/js/components.js` | Dialogs had no focus trap, no Escape, no focus restore | Shared `mountDialog()` adds all three plus backdrop dismissal; `closeActiveDialog()` |
| 9 | Numbers are locale-aware | `components.js` | Raw numbers in the usage panel | `Intl.NumberFormat()` for requests, tokens, latency and errors |
| 10 | Listbox needs keyboard support | `components.js` | `role="listbox"` with click handlers only | `ArrowDown/ArrowUp/Home/End/Escape` navigation, `aria-expanded` sync, outside-click close |
| 11 | Preserve navigation state | `web/js/app.js` | View changes were not reflected in the URL or restored on reload | `syncViewUrl()` writes `?view=…` via `history.replaceState`; boot reads it back |
| 12 | No endless autoplay motion | `web/js/motion.js` | Infinite halo loop with no control | Single settling tween; only user-triggered listening pulse remains |
| 13 | Scoped scroll containers | `app.css` | Scrolling a menu/panel scrolled the page | `overscroll-behavior: contain` on `.model-menu`, `.context`, `.modal-backdrop` |
| 14 | Anchored headings | `app.css` | Section headings hidden under sticky chrome | `.panel-section h3 { scroll-margin-top: var(--s-4) }` |
| 15 | Touch targets / mobile chrome | `app.css` | Tap highlight, 300ms delay, notch overlap | `touch-action: manipulation`, transparent tap highlight, `env(safe-area-inset-*)` padding |
| 16 | Readable headings | `app.css` | Orphaned words on narrow screens | `text-wrap: balance` on headings |
| 17 | Correct in-page links | `index.html` | Brand linked to non-existent `#chat` | Changed to `#mainView`; brand word given `translate="no"` |
| 18 | Focus after re-render | `app.js` | Memory filter chips lost focus when the list re-rendered | Focus restored to the matching chip |
| 19 | Busy-state feedback | `app.js`, `app.css` | No visible feedback while streaming | `.send-btn.is-busy` spinner + `.stream-status` with `role="status"`, `aria-live="polite"` |
| 20 | Live regions with correct politeness | `index.html` | Toast/stream updates could be noisy | `#toasts` `aria-live="polite" aria-atomic="false"`; `#streamStatus` `role="status"`; `#stream` `aria-live="off"` |
| 21 | Dialog forms | `components.js` | Inputs lacked `name`/`autocomplete`; placeholders ended abruptly | Added proper attributes and ellipsis-ended placeholders; `toolDialog()` resolves `null` on cancel/backdrop/Escape |
| 22 | Do not lose in-flight work | `app.js` | Reload during streaming silently dropped it | `beforeunload` guard while a stream is active |
| 23 | Smooth scrolling respects preference | `app.js` | Unconditional `behavior: 'smooth'` | `prefersReducedMotion() ? 'auto' : 'smooth'` |
| 24 | Mobile drawer must not cover content | `app.js`, `components.js` | Context panel was open by default on narrow screens | Collapsed by default at small widths; regression asserts `mobile_drawer_collapsed: true` |
| 25 | No template-y decorative text | `index.html` | Tracked-out ALL-CAPS kicker above the empty state | Removed `.empty-kicker` and the accent-coloured headline word |

## 3. Keyboard map

| Key | Context | Behaviour |
| --- | --- | --- |
| `Tab` / `Shift+Tab` | Whole app | Natural order; `:focus-visible` ring; dialogs are trapped |
| `Enter` | First focus | Skip link jumps to the conversation |
| `Escape` | Dialog | Closes and restores focus to the trigger |
| `Escape` | Model listbox | Closes the menu and returns focus to the trigger |
| `ArrowDown` / `ArrowUp` | Model listbox trigger or menu | Opens / moves between options with wraparound |
| `Home` / `End` | Model listbox menu | First / last option |
| `Enter` / `Space` | Model trigger | Opens the listbox |
| `Escape` | App | Also closes any active dialog (`ui.closeActiveDialog()`) |

## 4. Focus management

`mountDialog()` records `document.activeElement`, mounts the dialog, computes
focusable elements (buttons, inputs, selects, textareas, links, non-negative
`tabindex`), traps `Tab`, and restores focus to the previous element on close —
but only if it is still connected to the DOM. Backdrop clicks and Escape both
dismiss. This single implementation is shared by `confirmDialog`,
`memoryDialog`, `toolDialog` and `forgetDialog`, so no dialog can diverge.

## 5. Forms and internationalisation

- Every text input has a `name`, a visible `<label>`, an `autocomplete` value
  and an explicit `spellcheck` setting.
- User-supplied values are escaped with `esc()`; streaming content is written
  with `textContent`.
- Numbers use `Intl.NumberFormat()`. The voice surface requests `en-NG` where
  the Web Speech API supports it, and the brand word is marked `translate="no"`.
- The composer's placeholder ends with an ellipsis, and error text is a full
  sentence rather than a truncated fragment.

## 6. Motion and vestibular safety

See [ANIMATION-GUIDELINES.md](ANIMATION-GUIDELINES.md) §6. Three independent
guards (JS early return, zeroed motion tokens, forced CSS durations) honour
`prefers-reduced-motion`. The only infinite animation is a user-triggered voice
pulse that is killed when the session stops.

## 7. Honest verification and remaining gaps

**Verified in the Chromium regression**: dialog focus trap and focus
restoration, listbox arrow-key navigation, `?view=` URL state, chip focus after
re-render, streaming chat, mobile drawer collapsed by default, desktop and
mobile screenshots, and zero `console.error` / `pageerror` events.

**Workspace drawer** (`web/js/workspace.js`, checked by
`scripts/verify_workspace_ui.py`):

| Property | Implementation |
|---|---|
| Role | `role="dialog" aria-modal="true"`, `aria-labelledby` the heading, `aria-hidden` toggled with the open state |
| Focus in / out | Focus moves to the close button on open and back to the rail button on close |
| Focus trap | Tab and Shift+Tab wrap inside the drawer; 28 consecutive Tabs stay inside |
| Escape | Closes the innermost layer first — the image viewer, then the drawer |
| Tabs | `role="tablist"`, each tab `aria-selected` + `aria-controls`; panels answer to that id with `role="tabpanel"` and `aria-labelledby`; Arrow keys, Home and End move between tabs |
| Motion | Drawer and scrim transitions are removed under `prefers-reduced-motion: reduce` |
| Contrast | Uses the existing `--paper`/`--ink`/`--accent` tokens; not machine-measured (see below) |

**Not yet verified (open work)**:

- No automated axe-core / WCAG contrast audit has been run. Colour pairs are
  defined in `tokens.css` using `oklch()`, but contrast ratios have not been
  machine-measured.
- No screen-reader end-to-end pass (NVDA / VoiceOver / Orca) was performed in
  this environment.
- The inherited Qt desktop shell (`src/desktop_app/`) was rebranded but not
  re-audited for accessibility in this pass.
- Web Speech API availability varies; `web/js/voice.js` reports plainly when
  STT/TTS is unavailable instead of faking a waveform. That fallback has not
  been exercised on a device with no speech engine.
- Reduced-motion behaviour is implemented and reviewed but not yet asserted by
  an automated browser test.
