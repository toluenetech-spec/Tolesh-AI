# Tolesh Component Library

> Reusable, framework-free components and templates. No bundler, no build step,
> no runtime dependencies beyond the locally vendored GSAP file. The library is
> the actual code in [`web/`](web/), not a proposal.

## 1. How to consume it

Load order in [`web/index.html`](web/index.html):

```html
<link rel="stylesheet" href="./styles/fonts.css" />
<link rel="stylesheet" href="./styles/tokens.css" />
<link rel="stylesheet" href="./styles/app.css" />
<script src="./vendor/gsap.min.js"></script>
<script type="module" src="./js/app.js"></script>
```

`app.js` is the composition root. `components.js` exports renderers that take a
container plus data/callbacks and own their event wiring. `api.js` owns all
HTTP/SSE traffic, `motion.js` owns animation, `voice.js` owns the Web Speech
API surface. Nothing reaches into another module's DOM without a documented id.

### DOM root contract

These ids are the stable contract between markup and modules:

| Id | Role | Owner |
| --- | --- | --- |
| `mainView` | `<main>`; skip-link target; `tabindex="-1"` | `index.html`, `app.js` |
| `stream` / `messages` | Conversation stream and message list | `components.js` |
| `input` / `composer` / `sendBtn` | Composer | `app.js` |
| `voiceBtn` / `voiceOrb` / `orbCaption` | Voice entry points | `voice.js`, `motion.js` |
| `contextPanel` / `contextToggle` / `contextClose` | Context drawer | `app.js` |
| `modelSelector`, `memoryFilters`, `dialogRoot`, `toasts`, `mobileNav` | Panel mount points | `components.js`, `app.js` |

Decorative layers (`.atmosphere`, `.grain`, `.icon-sprite`) are always
`aria-hidden="true"` and never receive focus.

## 2. Design tokens

[`web/styles/tokens.css`](web/styles/tokens.css) is the single source of truth.
All colours are `oklch()`. See [DESIGN.md](DESIGN.md) for the rationale.

| Group | Tokens |
| --- | --- |
| Surfaces | `--ink-950` … `--ink-700` (warm near-black, hue 62) |
| Lines | `--line`, `--line-soft`, `--line-strong` |
| Type | `--ivory`, `--ivory-dim`, `--muted`, `--muted-dim` |
| Accents | `--brass`, `--brass-bright`, `--brass-deep`, `--brass-08/14/24`, `--terracotta`, `--indigo`, `--indigo-soft` |
| Semantic | `--ok`, `--warn`, `--danger`, `--danger-12` |
| Fonts | `--font-display` (Bodoni Moda), `--font-body` (Jost), `--font-mono` |
| Type scale | `--fs-2xs` … `--fs-3xl` (11px → 72px) |
| Spacing | `--s-1` … `--s-16` (4px base) |
| Radii | `--r-xs` … `--r-xl`, `--r-pill` |
| Elevation | `--shadow-sm/md/lg`, `--glow-brass` |
| Motion | `--ease-out`, `--ease-in-out`, `--dur-fast` (150ms), `--dur` (220ms), `--dur-slow` (420ms) |
| Layout / z-index | `--rail-w`, `--context-w`, `--topbar-h`, `--z-rail` … `--z-toast` |

Responsive breakpoints in `app.css`: `1180px`, `980px`, `680px`, plus the
`prefers-reduced-motion` block.

## 3. CSS components

| Group | Classes | State modifiers |
| --- | --- | --- |
| Shell | `.app-shell`, `.rail`, `.main`, `.topbar`, `.context` | `.context-collapsed` |
| Navigation | `.brand`, `.brand-mark`, `.brand-word`, `.rail-nav`, `.rail-item`, `.rail-label`, `.mobile-nav` | `.is-active` |
| Voice | `.voice-orb`, `.orb-ring`, `.orb-caption`, `.connection`, `.connection-dot` | `.is-listening`, `.is-online`, `.is-offline` |
| Model selector | `.model-selector`, `.model-trigger`, `.model-swatch`, `.model-menu`, `.model-group-label`, `.model-option`, `.opt-*` | `.is-open`, `.is-selected`, `.is-unconfigured`, `.is-on` |
| Conversation | `.stream`, `.empty-state`, `.empty-rule`, `.empty-title`, `.empty-sub`, `.suggestions`, `.suggestion`, `.messages`, `.message`, `.avatar`, `.bubble`, `.message-meta`, `.message-body`, `.caret` | `.is-user`, `.is-assistant`, `.is-error` |
| Composer | `.composer-wrap`, `.composer`, `.composer-actions`, `.stream-status`, `.composer-hint`, `.composer-footnote`, `.mic-btn`, `.send-btn` | `:focus-within`, `.is-active`, `.is-busy`, `:disabled` |
| Context panels | `.context-head`, `.panel-section`, `.panel-head`, `.panel-badge`, `.engine-grid`, `.engine-cell`, `.permission-list`, `.permission-card`, `.permission-actions`, `.mini-btn` | `.approve`, `.deny` |
| Memory | `.memory-filters`, `.chip`, `.memory-list`, `.memory-item`, `.danger-link` | `.chip.is-active` |
| Provider / usage / tools | `.provider-list`, `.provider-row`, `.status-dot`, `.usage-grid`, `.usage-cell`, `.tool-list`, `.tool-row`, `.risk-tag` | `.risk-safe`, `.risk-elevated`, `.risk-consequential`, `.is-on` |
| Feedback | `.toasts`, `.toast`, `.modal-backdrop`, `.modal`, `.modal-actions`, `.btn`, `.btn-primary`, `.btn-danger`, `.skip-link`, `.sr-only` | `.is-error`, `.is-ok` |

Utility conventions: `:focus-visible` is the only focus ring; all transitions
name explicit properties (never `all`); scrollable overlays set
`overscroll-behavior: contain`; `.panel-section h3` sets `scroll-margin-top`.

## 4. JS component contracts

[`web/js/components.js`](web/js/components.js) — all exports:

| Export | Signature | Responsibility |
| --- | --- | --- |
| `esc(s)` | `(s) => string` | HTML-escape every interpolated value |
| `icon(id, cls)` | `(id, cls='') => string` | `<svg><use href="#i-*"></svg>` from the inline sprite |
| `closeActiveDialog()` | `() => void` | Dismiss the mounted dialog via its stored closer |
| `toast(message, kind)` | `(string, 'info'\|'ok'\|'error') => void` | Transient feedback in `#toasts` |
| `createMessage(role, text, opts)` | `(role, text, {streaming, model}) => HTMLLIElement` | Message row with optional streaming caret |
| `setMessageText(li, text, streaming)` | `(HTMLLIElement, string, boolean) => void` | Update a streaming bubble via `textContent` |
| `finishMessage(li, modelLabel)` | `(li, string) => void` | Remove caret, stamp the model used |
| `renderModelSelector(container, opts)` | `({providers, models, current, onSelect}) => void` | Listbox with full keyboard navigation |
| `renderEngine(container, data)` | `(el, health) => void` | Status/version/provider cells |
| `renderPermissions(container, items, cb)` | `(el, items, {onApprove, onDeny}) => void` | Approval cards |
| `renderMemory(container, items, cb)` | `(el, items, {onDelete}) => void` | Deletable memory rows |
| `renderProviders(container, providers)` | `(el, providers) => void` | Provider readiness list |
| `renderUsage(container, usage)` | `(el, usage) => void` | `Intl.NumberFormat` metric cells |
| `renderTools(container, tools, cb)` | `(el, tools, {onInvoke}) => void` | Tool rows with risk tags |
| `renderSuggestions(container, items, onPick)` | `(el, string[], fn) => void` | Empty-state prompts |
| `confirmDialog`, `memoryDialog`, `toolDialog`, `forgetDialog` | `(opts) => Promise/void` | Mount through `mountDialog()` |
| `mountDialog(html, onDismiss)` | internal | Focus trap, Escape, backdrop, focus restore |

[`web/js/api.js`](web/js/api.js): `get`, `post`, `patch`, `del`, `health`,
`chatStream(body, onEvent, signal)` — the last decodes `data:` frames into
`RouterEvent` objects. [`web/js/motion.js`](web/js/motion.js):
`prefersReducedMotion`, `initMotion`, `animateIn`, `setOrbListening`.
[`web/js/voice.js`](web/js/voice.js): `VoiceController` (Web Speech API).

## 5. Reusable templates

These are verbatim structures from `components.js` — copy them when adding a
new view rather than inventing new markup.

**Message row**

```html
<li class="message is-assistant">
  <div class="avatar" aria-hidden="true">T</div>
  <div class="bubble">
    <div class="message-meta"><span class="who">Tolesh</span><span></span></div>
    <div class="message-body"></div>
  </div>
</li>
```

**Permission card**

```html
<div class="permission-card" data-id="…">
  <span class="risk">elevated action</span>
  <p class="tool">write_text_file</p>
  <p class="args">{"path":"…"}</p>
  <div class="permission-actions">
    <button class="mini-btn approve">Approve</button>
    <button class="mini-btn deny">Deny</button>
  </div>
</div>
```

**Memory item**

```html
<div class="memory-item" data-id="…">
  <div class="body"><span class="cat">Preference</span><p class="txt">…</p></div>
  <button class="del" aria-label="Delete this memory" title="Delete">…</button>
</div>
```

**Model listbox option**

```html
<div class="model-menu" id="modelMenu" role="listbox" aria-label="Choose a model">
  <button class="model-option is-selected" data-model="auto"
          role="option" aria-selected="true">
    <span class="opt-text"><span class="opt-name">Auto · best available</span>
    <span class="opt-meta">Route by task, fall back automatically</span></span>
    <span class="opt-tag">recommended</span>
  </button>
</div>
```

**Usage metric cells**

```html
<div class="usage-grid">
  <div class="usage-cell"><span class="n">1,204</span><span class="l">Tokens</span></div>
  <div class="usage-cell"><span class="n">18ms</span><span class="l">Avg latency</span></div>
</div>
```

**Dialog skeleton** — every dialog goes through `mountDialog(html)`, which adds
the focus trap, Escape-to-close, backdrop dismissal and focus restoration:

```html
<div class="modal-backdrop">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="dlgTitle">
    <h2 id="dlgTitle">…</h2>
    <p>…</p>
    <label for="f">…</label>
    <input id="f" name="…" autocomplete="…" spellcheck="…" />
    <div class="modal-actions">
      <button class="btn">Cancel</button>
      <button class="btn btn-primary">Confirm</button>
    </div>
  </div>
</div>
```

## 6. State-class conventions

`is-*` describes a transient state on the element itself (`is-active`,
`is-open`, `is-selected`, `is-busy`, `is-listening`, `is-online`); parent-level
states live on the shell (`.app-shell.context-collapsed`). Risk is always a
`risk-*` class paired with a visible text label, never colour alone. All
interpolated text passes through `esc()`; streaming text is assigned with
`textContent`, never `innerHTML`.

## 7. Adding a component

1. Add tokens to `tokens.css` if a new colour/size is needed — never a literal.
2. Add styles to `app.css` using explicit transition properties.
3. Add a renderer to `components.js` that takes `(container, data, callbacks)`.
4. Give decorative icons `aria-hidden="true"`; give interactive elements an
   accessible name and a visible `:focus-visible` state.
5. Wire it in `app.js` through `api.js`, never a direct `fetch`.
6. Verify at 1440×900 and 390×844, with keyboard only, and with reduced motion.

## 8. Backend contract

Renderers consume the shapes defined in
[`src/tolesh/server/schemas.py`](src/tolesh/server/schemas.py) and the
`RouterEvent` SSE frames from
[`src/tolesh/ai/router.py`](src/tolesh/ai/router.py). See
[ARCHITECTURE.md](ARCHITECTURE.md) for the endpoint list and
[ACCESSIBILITY.md](ACCESSIBILITY.md) for the audit that shaped these contracts.
