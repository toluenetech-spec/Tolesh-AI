/* Reusable, self-contained Tolesh UI components.
   Every dynamic string is escaped. Streaming text is written with textContent;
   an assistant reply is converted to HTML only once, by renderMarkdown(), which
   escapes first and can emit a fixed set of tags. See COMPONENT-LIBRARY.md. */

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

export function icon(id, cls = '') {
  return `<svg class="icon ${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;
}

/* --------------------------------------------------------------- markdown */
/* Escape-first Markdown for assistant replies.
 *
 * Model output is untrusted text. Every character is escaped before any tag is
 * produced, and the only tags this function can emit are a fixed list —
 * p, strong, em, code, pre, ul, ol, li, h3–h6, blockquote, hr, table and
 * http(s) anchors. A reply containing <img onerror=…> or [x](javascript:…) is
 * therefore inert: the angle brackets are already entities and the link
 * pattern only matches http/https.
 *
 * Streaming text stays plain (see setMessageText); this runs once, when the
 * message is finished, so a half-arrived "**bold" never renders as garbage. */

function inlineMd(s) {
  const codes = [];
  let t = s.replace(/`([^`\n]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
  t = t.replace(/\[([^\]]{1,200})\]\((https?:\/\/[^\s)]{1,500})\)/g,
    (_, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`);
  t = t.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  t = t.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  t = t.replace(/(^|[\s(])_([^_\n]+)_/g, '$1<em>$2</em>');
  return t.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
}

const rowCells = (line) => line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|');

export function renderMarkdown(text) {
  const lines = esc(text ?? '').replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let para = [];
  let list = null;
  let quote = [];
  let fence = null;
  let table = null;

  const flushPara = () => {
    if (para.length) { out.push(`<p>${inlineMd(para.join(' '))}</p>`); para = []; }
  };
  const flushList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const flushQuote = () => {
    if (quote.length) { out.push(`<blockquote>${inlineMd(quote.join(' '))}</blockquote>`); quote = []; }
  };
  const flushTable = () => {
    if (!table) return;
    const [head, ...rows] = table;
    const cells = (r, tag) => r.map((c) => `<${tag}>${inlineMd(c.trim())}</${tag}>`).join('');
    out.push(`<div class="md-table"><table><thead><tr>${cells(head, 'th')}</tr></thead><tbody>`
      + rows.map((r) => `<tr>${cells(r, 'td')}</tr>`).join('') + '</tbody></table></div>');
    table = null;
  };
  const flushAll = () => { flushPara(); flushList(); flushQuote(); flushTable(); };

  for (let i = 0; i < lines.length; i += 1) {
    const raw = lines[i];
    const line = raw.replace(/\s+$/, '');

    if (fence) {
      if (/^\s*```/.test(line)) {
        out.push(`<pre><code>${fence.buf.join('\n')}</code></pre>`);
        fence = null;
      } else {
        fence.buf.push(raw);
      }
      continue;
    }
    const open = line.match(/^\s*```(\w*)\s*$/);
    if (open) { flushAll(); fence = { lang: open[1], buf: [] }; continue; }

    if (!line.trim()) { flushAll(); continue; }

    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { flushAll(); out.push('<hr>'); continue; }

    const h = line.match(/^\s*(#{1,4})\s+(.*)$/);
    if (h) {
      flushAll();
      const lvl = Math.min(h[1].length + 2, 6);
      out.push(`<h${lvl}>${inlineMd(h[2])}</h${lvl}>`);
      continue;
    }

    const q = line.match(/^\s*>\s?(.*)$/);
    if (q) { flushPara(); flushList(); flushTable(); quote.push(q[1]); continue; }
    flushQuote();

    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    const ol = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    if (ul || ol) {
      flushPara(); flushTable();
      const want = ul ? 'ul' : 'ol';
      if (list !== want) {
        flushList();
        // A code block between two numbered items ends the list; the next item
        // keeps its own number instead of restarting at 1.
        if (want === 'ol') {
          const n = Number(ol[1]);
          out.push(n > 1 ? `<ol start="${n}">` : '<ol>');
        } else {
          out.push('<ul>');
        }
        list = want;
      }
      out.push(`<li>${inlineMd(ul ? ul[1] : ol[2])}</li>`);
      continue;
    }
    flushList();

    const next = (lines[i + 1] || '').trim();
    if (line.includes('|') && next.includes('-') && /^[\s|:-]+$/.test(next)) {
      flushPara();
      table = [rowCells(line)];
      i += 1;
      while (i + 1 < lines.length && (lines[i + 1] || '').includes('|')) {
        i += 1;
        table.push(rowCells(lines[i]));
      }
      flushTable();
      continue;
    }

    para.push(line.trim());
  }

  flushAll();
  if (fence) out.push(`<pre><code>${fence.buf.join('\n')}</code></pre>`);
  return out.join('');
}

/* Shared modal plumbing. Every dialog gets a focus trap, Escape-to-close,
   backdrop dismissal and focus restoration to the element that opened it. */
export function closeActiveDialog() {
  const root = document.getElementById('dialogRoot');
  if (typeof root.__toleshClose === 'function') root.__toleshClose();
  else root.innerHTML = '';
}

function mountDialog(html, onDismiss = () => {}) {
  const root = document.getElementById('dialogRoot');
  const previous = document.activeElement;
  root.innerHTML = html;
  const backdrop = root.querySelector('.modal-backdrop');
  const dialog = root.querySelector('.modal');
  const focusables = () => [...dialog.querySelectorAll(
    'button, input, select, textarea, a[href], [tabindex]:not([tabindex="-1"])'
  )].filter((el) => !el.disabled && el.getClientRects().length > 0);
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key !== 'Tab') return;
    const f = focusables();
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  const close = () => {
    document.removeEventListener('keydown', onKey, true);
    delete root.__toleshClose;
    root.innerHTML = '';
    if (previous && previous.isConnected) previous.focus();
    onDismiss();
  };
  root.__toleshClose = close;
  document.addEventListener('keydown', onKey, true);
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
  return { root, dialog, close, backdrop };
}

/* ------------------------------------------------------------------ toasts */
export function toast(message, kind = 'info') {
  const host = document.getElementById('toasts');
  if (!host) return;
  // Three at a time. A burst of confirmations must not become a wall that
  // covers the panel the user is working in.
  while (host.children.length >= 3) host.firstElementChild.remove();
  const el = document.createElement('div');
  el.className = `toast is-${kind}`;
  const glyph = kind === 'error' ? 'info' : kind === 'ok' ? 'check' : 'bolt';
  el.innerHTML = icon(glyph) + `<span>${esc(message)}</span>`;
  host.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity 220ms, transform 220ms';
    el.style.opacity = '0';
    el.style.transform = 'translateY(8px)';
    setTimeout(() => el.remove(), 240);
  }, 4200);
}

/* ---------------------------------------------------------------- messages */
export function createMessage(role, text, { streaming = false, model = '' } = {}) {
  const li = document.createElement('li');
  li.className = `message is-${role}${role === 'error' ? ' is-error' : ''}`;
  const who = role === 'user' ? 'You' : role === 'error' ? 'Tolesh' : 'Tolesh';
  const meta = model ? ` · ${esc(model)}` : '';
  li.innerHTML = `
    <div class="avatar" aria-hidden="true">${role === 'user' ? 'Y' : 'T'}</div>
    <div class="bubble">
      <div class="message-meta"><span class="who">${esc(who)}</span><span>${meta}</span></div>
      <div class="message-body"></div>
    </div>`;
  const body = li.querySelector('.message-body');
  body.textContent = text || '';
  if (streaming) {
    const caret = document.createElement('span');
    caret.className = 'caret';
    caret.setAttribute('aria-hidden', 'true');
    body.appendChild(caret);
  }
  return li;
}

export function setMessageText(li, text, streaming = false) {
  const body = li.querySelector('.message-body');
  if (!body) return;
  body.textContent = text;
  if (streaming) {
    const caret = document.createElement('span');
    caret.className = 'caret';
    caret.setAttribute('aria-hidden', 'true');
    body.appendChild(caret);
  }
}

export function finishMessage(li, modelLabel) {
  const caret = li.querySelector('.caret');
  if (caret) caret.remove();
  const body = li.querySelector('.message-body');
  // Only assistant replies are treated as Markdown: what the user typed is
  // shown exactly as typed.
  if (body && li.classList.contains('is-assistant')) {
    body.classList.add('is-markdown');
    body.innerHTML = renderMarkdown(body.textContent || '');
  }
  if (modelLabel) {
    const meta = li.querySelector('.message-meta span:last-child');
    if (meta) meta.textContent = ` · ${modelLabel}`;
  }
}

/* -------------------------------------------------------- model selector */
export function renderModelSelector(container, { providers, models, current, onSelect }) {
  const byProvider = {};
  for (const m of models) (byProvider[m.provider] ||= []).push(m);
  const providerMap = Object.fromEntries(providers.map((p) => [p.name, p]));
  const selected = current || 'auto';
  const currentModel = models.find((m) => m.id === selected);
  const label = selected === 'auto' ? 'Auto · best available' : (currentModel?.label || selected);
  const swatchClass = selected === 'auto' ? '' :
    (providerMap[currentModel?.provider]?.configured ? '' : 'is-unconfigured');

  const groups = Object.entries(byProvider).map(([pname, list]) => {
    const p = providerMap[pname];
    const dot = `<span class="status-dot ${p?.configured ? 'is-on' : ''}"></span>`;
    const opts = list.map((m) => `
      <button class="model-option ${m.id === selected ? 'is-selected' : ''}" data-model="${esc(m.id)}" role="option" aria-selected="${m.id === selected}">
        ${icon('check')}
        <span class="opt-text"><span class="opt-name">${esc(m.label)}</span>
        <span class="opt-meta">${esc(m.model)} · ${esc(m.cost_tier)}</span></span>
        <span class="opt-tag">${p?.configured ? '' : 'needs key'}</span>
      </button>`).join('');
    return `<div class="model-group" role="group" aria-label="${esc(p?.label || pname)}">
      <div class="model-group-label">${dot}${esc(p?.label || pname)}</div>${opts}</div>`;
  }).join('');

  container.innerHTML = `
    <button class="model-trigger" id="modelTrigger" aria-haspopup="listbox" aria-expanded="false">
      <span class="model-swatch ${swatchClass}"></span>
      <span class="model-text">
        <span class="model-kicker">Model</span>
        <span class="model-name">${esc(label)}</span>
      </span>
      ${icon('chevron')}
    </button>
    <div class="model-menu" id="modelMenu" role="listbox" aria-label="Choose a model">
      <button class="model-option ${selected === 'auto' ? 'is-selected' : ''}" data-model="auto" role="option" aria-selected="${selected === 'auto'}">
        ${icon('check')}
        <span class="opt-text"><span class="opt-name">Auto · best available</span>
        <span class="opt-meta">Route by task, fall back automatically</span></span>
        <span class="opt-tag">recommended</span>
      </button>
      ${groups}
    </div>`;

  const root = container.closest('.model-selector') || container;
  const trigger = container.querySelector('#modelTrigger');
  const menu = container.querySelector('#modelMenu');
  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = root.classList.toggle('is-open');
    trigger.setAttribute('aria-expanded', String(open));
  });
  container.querySelectorAll('.model-option').forEach((btn) => {
    btn.addEventListener('click', () => {
      root.classList.remove('is-open');
      trigger.setAttribute('aria-expanded', 'false');
      onSelect(btn.dataset.model === 'auto' ? null : btn.dataset.model);
    });
  });
  // role="listbox" needs keyboard navigation, not just click handlers.
  const options = [...container.querySelectorAll('.model-option')];
  const setOpen = (open) => {
    root.classList.toggle('is-open', open);
    trigger.setAttribute('aria-expanded', String(open));
  };
  trigger.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
      if (e.key !== 'Enter' && e.key !== ' ') e.preventDefault();
      setOpen(true);
      (options.find((o) => o.classList.contains('is-selected')) || options[0])?.focus();
    }
  });
  menu.addEventListener('keydown', (e) => {
    if (!root.classList.contains('is-open') || !options.length) return;
    const i = options.indexOf(document.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); setOpen(false); trigger.focus(); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const step = e.key === 'ArrowDown' ? 1 : -1;
      options[(i + step + options.length) % options.length].focus();
    } else if (e.key === 'Home') { e.preventDefault(); options[0].focus(); }
    else if (e.key === 'End') { e.preventDefault(); options[options.length - 1].focus(); }
  });
  document.addEventListener('click', (e) => {
    if (!root.contains(e.target)) {
      root.classList.remove('is-open');
      trigger.setAttribute('aria-expanded', 'false');
    }
  });
}

/* --------------------------------------------------------------- panels */
export function renderEngine(container, data) {
  const cells = [
    ['Status', data.status || '—'],
    ['Version', `v${data.version || '—'}`],
    ['Providers configured', String((data.providers_configured || []).length)],
    ['Default model', data.default_model || 'auto'],
  ];
  container.innerHTML = cells.map(([k, v]) =>
    `<div class="engine-cell"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`
  ).join('');
}

export function renderPermissions(container, items, { onApprove, onDeny }) {
  if (!items.length) {
    container.innerHTML = `<p class="empty-line">Nothing awaiting approval. Safe actions are audited automatically.</p>`;
    return;
  }
  container.innerHTML = items.map((it) => `
    <div class="permission-card" data-id="${esc(it.id)}">
      <span class="risk">${esc(it.risk)} action</span>
      <p class="tool">${esc(it.tool)}</p>
      <p class="args">${esc(JSON.stringify(it.args || {}))}</p>
      <div class="permission-actions">
        <button class="mini-btn approve" data-act="approve">${icon('check')} Approve</button>
        <button class="mini-btn deny" data-act="deny">${icon('close')} Deny</button>
      </div>
    </div>`).join('');
  container.querySelectorAll('.permission-card').forEach((card) => {
    const id = card.dataset.id;
    card.querySelector('[data-act="approve"]').addEventListener('click', () => onApprove(id));
    card.querySelector('[data-act="deny"]').addEventListener('click', () => onDeny(id));
  });
}

export function renderMemory(container, items, { onDelete }) {
  if (!items.length) {
    container.innerHTML = `<p class="empty-line">No long-term memory yet. Tolesh only stores what you explicitly add.</p>`;
    return;
  }
  container.innerHTML = items.map((it) => `
    <div class="memory-item" data-id="${esc(it.id)}">
      <div class="body"><span class="cat">${esc(it.category_label || it.category)}</span>
      <p class="txt">${esc(it.content)}</p></div>
      <button class="del" aria-label="Delete this memory" title="Delete">${icon('trash')}</button>
    </div>`).join('');
  container.querySelectorAll('.del').forEach((btn) => {
    btn.addEventListener('click', () => onDelete(btn.closest('.memory-item').dataset.id));
  });
}

export function renderProviders(container, providers, { onConfigure } = {}) {
  container.innerHTML = providers.map((p) => `
    <div class="provider-row" title="${esc(p.notes || '')}">
      <span class="status-dot ${p.configured ? 'is-on' : ''}"></span>
      <span class="pname">${esc(p.label)}</span>
      <span class="pmeta">${p.configured ? 'ready' : 'no key'}</span>
      <button class="text-btn provider-config" data-configure="${esc(p.name)}"
              aria-label="Configure ${esc(p.label)}">${icon('sliders')}</button>
    </div>`).join('');
  if (typeof onConfigure === 'function') {
    container.querySelectorAll('[data-configure]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const provider = providers.find((p) => p.name === btn.dataset.configure);
        if (provider) onConfigure(provider);
      });
    });
  }
}

/** The chain the router will walk, with the two controls that change it.
 *  Order matters here: position 1 is what answers unless it fails. */
export function renderRoute(container, items, { onMove, onToggle } = {}) {
  if (!items.length) {
    container.innerHTML = '<p class="panel-note">No model is configured yet. Add a key to a provider first.</p>';
    return;
  }
  const live = items.filter((m) => m.enabled).length;
  container.innerHTML = items.map((m, i) => `
    <div class="route-row ${m.enabled ? '' : 'is-off'}" data-model="${esc(m.id)}">
      <span class="route-pos">${m.enabled ? i + 1 : '–'}</span>
      <span class="route-text" title="${esc(m.label)} — ${esc(m.provider)}">
        <span class="route-name">${esc(m.label)}</span>
        <span class="route-meta">${esc(m.provider)}${m.enabled ? '' : ' · off'}</span>
      </span>
      <span class="route-actions">
        <button class="text-btn" data-move="up" data-id="${esc(m.id)}"
                aria-label="Move ${esc(m.label)} up"
                ${(!m.enabled || i === 0) ? 'disabled' : ''}>${icon('chevron', 'rot-up')}</button>
        <button class="text-btn" data-move="down" data-id="${esc(m.id)}"
                aria-label="Move ${esc(m.label)} down"
                ${(!m.enabled || i >= live - 1) ? 'disabled' : ''}>${icon('chevron', 'rot-down')}</button>
        <label class="route-switch" title="${m.provider === 'offline' ? 'The last-resort engine cannot be switched off' : (m.enabled ? 'Switch off' : 'Switch on')}">
          <input type="checkbox" data-toggle="${esc(m.id)}" ${m.enabled ? 'checked' : ''}
                 ${m.provider === 'offline' ? 'disabled' : ''}
                 aria-label="Enable ${esc(m.label)}">
        </label>
      </span>
    </div>`).join('');
  container.querySelectorAll('[data-move]').forEach((btn) => {
    btn.addEventListener('click', () => onMove?.(btn.dataset.id, btn.dataset.move));
  });
  container.querySelectorAll('[data-toggle]').forEach((box) => {
    box.addEventListener('change', () => onToggle?.(box.dataset.toggle, box.checked));
  });
}

export function renderUsage(container, usage) {  const nf = new Intl.NumberFormat();
  const cells = [
    [nf.format(usage.requests ?? 0), 'Requests'],
    [nf.format(usage.total_tokens ?? 0), 'Tokens'],
    [`${nf.format(usage.avg_latency_ms ?? 0)}ms`, 'Avg latency'],
    [nf.format(usage.errors ?? 0), 'Errors'],
  ];
  container.innerHTML = cells.map(([n, l]) =>
    `<div class="usage-cell"><span class="n">${esc(n)}</span><span class="l">${esc(l)}</span></div>`
  ).join('');
}

export function renderTools(container, tools, { onInvoke }) {
  // A control this machine cannot perform is shown as unavailable, with the
  // reason in the tooltip, rather than offered and then failing.
  container.innerHTML = tools.map((t) => {
    const blocked = t.available === false;
    const title = t.detail ? ` title="${esc(t.detail)}"` : '';
    return `
    <div class="tool-row${blocked ? ' unavailable' : ''}" data-tool="${esc(t.name)}"${title}>
      <span class="tname">${esc(t.name)}</span>
      <span class="risk-tag risk-${esc(t.risk)}">${esc(t.risk)}</span>
      ${blocked
        ? '<span class="risk-tag">unavailable</span>'
        : `<button class="invoke" data-invoke="${esc(t.name)}">${icon('arrow')}</button>`}
    </div>`;
  }).join('');
  container.querySelectorAll('[data-invoke]').forEach((btn) => {
    btn.addEventListener('click', () => onInvoke(btn.dataset.invoke));
  });
}

export function renderSuggestions(container, items, onPick) {
  container.innerHTML = items.map((s) =>
    `<button class="suggestion" data-text="${esc(s)}">${icon('arrow')}${esc(s)}</button>`
  ).join('');
  container.querySelectorAll('.suggestion').forEach((btn) => {
    btn.addEventListener('click', () => onPick(btn.dataset.text));
  });
}

/* --------------------------------------------------------------- dialogs */
export function confirmDialog({ title, body, confirmLabel = 'Confirm', danger = false, onConfirm }) {
  const { root, close } = mountDialog(`
    <div class="modal-backdrop">
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle" aria-describedby="modalBody">
        <h2 id="modalTitle">${esc(title)}</h2>
        <p id="modalBody">${esc(body)}</p>
        <div class="modal-actions">
          <button class="btn" data-cancel>Cancel</button>
          <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-confirm>${esc(confirmLabel)}</button>
        </div>
      </div>
    </div>`);
  root.querySelector('[data-cancel]').addEventListener('click', close);
  root.querySelector('[data-confirm]').addEventListener('click', () => { close(); onConfirm(); });
  root.querySelector('[data-cancel]').focus();
}

export function memoryDialog({ onSubmit }) {
  const options = ['conversation', 'preference', 'personal', 'project', 'task']
    .map((c) => `<option value="${c}">${c[0].toUpperCase() + c.slice(1)}</option>`).join('');
  const { root, close } = mountDialog(`
    <div class="modal-backdrop">
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
        <h2 id="modalTitle">Add to memory</h2>
        <p>Tolesh stores exactly what you type here. You can see and delete it at any time.</p>
        <label for="memCategory">Kind</label>
        <select id="memCategory" name="category">${options}</select>
        <label for="memContent">What should Tolesh remember?</label>
        <textarea id="memContent" name="content" rows="3" autocomplete="off"
                  placeholder="e.g. I prefer short answers in Pidgin…"></textarea>
        <div class="modal-actions">
          <button class="btn" data-cancel>Cancel</button>
          <button class="btn btn-primary" data-save>Save</button>
        </div>
      </div>
    </div>`);
  root.querySelector('[data-cancel]').addEventListener('click', close);
  root.querySelector('[data-save]').addEventListener('click', () => {
    const category = root.querySelector('#memCategory').value;
    const content = root.querySelector('#memContent').value.trim();
    if (!content) { toast('Please type something to remember.', 'error'); return; }
    close();
    onSubmit(category, content);
  });
  root.querySelector('#memContent').focus();
}

/** Provider configuration dialog: endpoint, key, enabled state, the model list
 *  the endpoint actually serves, and a real connection test. The backend never
 *  echoes a stored key, so an empty key field means "keep the secret that is
 *  already saved". */
export function providerDialog(provider, { onSave, onTest, onDiscover } = {}) {
  const hasKey = provider.api_key === '***' || provider.configured;
  const declared = provider.models || [];
  const { root, close } = mountDialog(`
    <div class="modal-backdrop">
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
        <h2 id="modalTitle">${esc(provider.label)}</h2>
        <p>${esc(provider.notes || 'Configure this provider.')}</p>
        <label for="provBaseUrl">Base URL</label>
        <input id="provBaseUrl" name="base_url" autocomplete="off" spellcheck="false"
               value="${esc(provider.base_url || '')}" placeholder="https://api.example.com/v1" />
        <label for="provApiKey">API key</label>
        <input id="provApiKey" name="api_key" type="password" autocomplete="off" spellcheck="false"
               placeholder="${hasKey ? '•••• stored — leave blank to keep' : 'Paste your key'}" />
        <label class="inline-check">
          <input id="provEnabled" name="enabled" type="checkbox" ${provider.enabled ? 'checked' : ''} />
          <span>Enabled</span>
        </label>
        <label id="provModelsLabel">Models</label>
        <div class="model-pull">
          <button class="btn" type="button" data-load-models>Load model list from endpoint</button>
          <p class="modal-note" id="provModelsNote" role="status" aria-live="polite">${declared.length ? `${declared.length} declared model${declared.length === 1 ? '' : 's'} in use.` : 'No models declared yet.'}</p>
        </div>
        <div class="model-pull-list" id="provModels" hidden></div>
        <p class="modal-note" id="provTestResult" role="status" aria-live="polite"></p>
        <div class="modal-actions">
          <button class="btn" data-cancel>Cancel</button>
          <button class="btn" data-test>Test connection</button>
          <button class="btn btn-primary" data-save>Save</button>
        </div>
      </div>
    </div>`);

  const result = root.querySelector('#provTestResult');
  const modelsNote = root.querySelector('#provModelsNote');
  const modelsBox = root.querySelector('#provModels');
  const pullBtn = root.querySelector('[data-load-models]');
  let pulled = null;   // ids returned by the endpoint, or null when not loaded

  const checkedModels = () => [...modelsBox.querySelectorAll('input:checked')].map((i) => i.value);

  const patchFrom = () => {
    const patch = {
      base_url: root.querySelector('#provBaseUrl').value.trim(),
      enabled: root.querySelector('#provEnabled').checked,
    };
    const key = root.querySelector('#provApiKey').value;
    if (key) patch.api_key = key;
    if (pulled) {
      const chosen = checkedModels();
      if (chosen.length) patch.models = chosen;
    }
    return patch;
  };

  const showModels = (ids) => {
    pulled = ids;
    modelsBox.hidden = false;
    modelsBox.innerHTML = ids.map((id) => `
      <label class="inline-check">
        <input type="checkbox" value="${esc(id)}" checked />
        <span translate="no">${esc(id)}</span>
      </label>`).join('');
  };

  root.querySelector('[data-cancel]').addEventListener('click', close);
  root.querySelector('[data-save]').addEventListener('click', () => {
    const patch = patchFrom();
    close();
    if (typeof onSave === 'function') onSave(patch);
  });
  pullBtn.addEventListener('click', async () => {
    if (typeof onDiscover !== 'function') return;
    pullBtn.disabled = true;
    modelsNote.classList.remove('is-bad');
    modelsNote.textContent = 'Saving the key, then asking the endpoint…';
    try {
      // Save first: the discovery call must use the stored key, not form state
      // the backend has never seen.
      const r = await onDiscover(patchFrom());
      if (r && r.ok && Array.isArray(r.models) && r.models.length) {
        showModels(r.models);
        const ms = r.latency_ms != null ? ` in ${r.latency_ms} ms` : '';
        modelsNote.textContent = `The endpoint reported ${r.models.length} model${r.models.length === 1 ? '' : 's'}${ms}. Untick anything you do not want in the picker, then Save.`;
      } else {
        modelsNote.textContent = `${(r && r.status) || 'failed'}: ${(r && r.detail) || 'no models returned'}`;
        modelsNote.classList.add('is-bad');
      }
    } catch (err) {
      modelsNote.textContent = err.message;
      modelsNote.classList.add('is-bad');
    } finally {
      pullBtn.disabled = false;
    }
  });
  root.querySelector('[data-test]').addEventListener('click', async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    result.classList.remove('is-bad');
    result.textContent = 'Saving and testing…';
    try {
      const r = await onTest(patchFrom());
      if (r.ok) {
        result.textContent = `Connected — ${r.model} replied “${String(r.reply || '').slice(0, 40)}”.`;
      } else {
        const tried = (r.attempts || []).length;
        result.textContent = `${r.status}: ${r.detail || 'no answer'}${tried ? ` (${tried} model${tried === 1 ? '' : 's'} tried)` : ''}`;
        result.classList.add('is-bad');
      }
    } catch (err) {
      result.textContent = `Test failed: ${err.message}`;
      result.classList.add('is-bad');
    } finally {
      btn.disabled = false;
    }
  });
  root.querySelector('#provBaseUrl').focus();
}

/** Dialog for tools that need arguments. Returns a Promise resolving to the
 *  args object, or null when cancelled (including backdrop / Escape). */
export function toolDialog(name) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (!settled) { settled = true; resolve(v); } };
    const fields = {
      list_directory: `<label for="toolPath">Folder path</label><input id="toolPath" name="path" autocomplete="off" value="${esc(homeHint())}" placeholder="e.g. ~/Documents…" />`,
      read_text_file: `<label for="toolPath">File path</label><input id="toolPath" name="path" autocomplete="off" placeholder="e.g. ~/notes.txt…" />`,
      write_text_file: `<label for="toolPath">File path</label><input id="toolPath" name="path" autocomplete="off" placeholder="e.g. ~/tolesh-note.txt…" /><label for="toolContent">Content</label><textarea id="toolContent" name="content" rows="3" autocomplete="off" placeholder="Text to write…"></textarea>`,
      open_application: `<label for="toolName">Application</label><select id="toolName" name="application">${['file-manager', 'text-editor', 'terminal', 'browser', 'calculator', 'code-editor', 'music-player'].map((a) => `<option value="${a}">${a}</option>`).join('')}</select>`,
      delete_file: `<label for="toolPath">File path</label><input id="toolPath" name="path" autocomplete="off" placeholder="e.g. ~/file.txt…" />`,
      run_command: `<label for="toolCmd">Command</label><input id="toolCmd" name="command" autocomplete="off" spellcheck="false" placeholder="e.g. echo hello…" />`,
      set_volume: `<label for="toolLevel">Volume (0–100)</label><input id="toolLevel" name="level" type="number" min="0" max="100" step="1" value="50" />`,
      set_brightness: `<label for="toolLevel">Brightness (0–100)</label><input id="toolLevel" name="level" type="number" min="0" max="100" step="1" value="50" />`,
    };
    const { root, close } = mountDialog(`
      <div class="modal-backdrop">
        <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
          <h2 id="modalTitle">${esc(name)}</h2>
          <p>Safe actions run immediately. Anything consequential requires your approval in the Context panel afterwards.</p>
          ${fields[name] || ''}
          <div class="modal-actions">
            <button class="btn" data-cancel>Cancel</button>
            <button class="btn btn-primary" data-run>Run</button>
          </div>
        </div>
      </div>`, () => done(null));
    const val = (id) => root.querySelector(`#${id}`)?.value?.trim() || '';
    root.querySelector('[data-cancel]').addEventListener('click', () => { done(null); close(); });
    root.querySelector('[data-run]').addEventListener('click', () => {
      let args = {};
      if (name === 'list_directory' || name === 'read_text_file' || name === 'delete_file') args = { path: val('toolPath') };
      else if (name === 'write_text_file') args = { path: val('toolPath'), content: val('toolContent') };
      else if (name === 'open_application') args = { name: val('toolName') };
      else if (name === 'run_command') args = { command: val('toolCmd') };
      else if (name === 'set_volume' || name === 'set_brightness') {
        const level = Number(val('toolLevel'));
        if (!Number.isInteger(level) || level < 0 || level > 100) {
          root.querySelector('#toolLevel').focus();
          return;
        }
        args = { level };
      }
      done(args);
      close();
    });
    (root.querySelector('#toolPath') || root.querySelector('#toolName')
      || root.querySelector('#toolCmd') || root.querySelector('#toolLevel'))?.focus();
  });
}

function homeHint() { return '~'; }

export function forgetDialog({ onConfirm }) {
  const { root, close } = mountDialog(`
    <div class="modal-backdrop">
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
        <h2 id="modalTitle">Forget everything</h2>
        <p>This permanently deletes every long-term memory item. Conversations are kept separately. Type <strong>FORGET EVERYTHING</strong> to confirm.</p>
        <label for="forgetPhrase">Confirmation phrase</label>
        <input id="forgetPhrase" name="confirmation" autocomplete="off" spellcheck="false" placeholder="FORGET EVERYTHING" />
        <div class="modal-actions">
          <button class="btn" data-cancel>Cancel</button>
          <button class="btn btn-danger" data-confirm>Delete all memory</button>
        </div>
      </div>
    </div>`);
  root.querySelector('[data-cancel]').addEventListener('click', close);
  root.querySelector('[data-confirm]').addEventListener('click', () => {
    const phrase = root.querySelector('#forgetPhrase').value.trim();
    close();
    onConfirm(phrase);
  });
  root.querySelector('#forgetPhrase').focus();
}

/* --------------------------------------------------------------- history */
function relativeTime(seconds) {
  const delta = Math.max(0, Date.now() / 1000 - Number(seconds || 0));
  if (delta < 60) return 'just now';
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  if (delta < 604800) return `${Math.floor(delta / 86400)}d ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}

/** Conversation history: the list ChatGPT puts in the sidebar, with the
 *  actions that make it usable (pin, archive, export, delete). */
export function renderHistory(container, {
  conversations = [], activeId = null, query = '', archived = false,
  onOpen, onPin, onArchive, onExport, onDelete,
} = {}) {
  if (!conversations.length) {
    container.innerHTML = `<p class="panel-note">${query
      ? 'Nothing matched that search.'
      : archived
        ? 'No archived conversations.'
        : 'No conversations yet. Say something to start one.'}</p>`;
    return;
  }
  container.innerHTML = conversations.map((c) => `
    <div class="history-row ${c.id === activeId ? 'is-active' : ''} ${c.pinned ? 'is-pinned' : ''}"
         data-id="${esc(c.id)}">
      <button class="history-open" data-open="${esc(c.id)}" title="${esc(c.title)}">
        <span class="history-title">${esc(c.title)}</span>
        <span class="history-meta">${c.message_count || 0} msg · ${relativeTime(c.updated_at)}</span>
        ${c.snippet ? `<span class="history-snippet">${esc(c.snippet)}</span>` : ''}
      </button>
      <span class="history-actions">
        <button class="mini-btn" data-pin="${esc(c.id)}" aria-pressed="${c.pinned ? 'true' : 'false'}"
                title="${c.pinned ? 'Unpin' : 'Pin'}">${icon('pin')}</button>
        <button class="mini-btn" data-archive="${esc(c.id)}"
                title="${archived ? 'Restore' : 'Archive'}">${icon('archive')}</button>
        <button class="mini-btn" data-export="${esc(c.id)}" title="Export as Markdown">${icon('download')}</button>
        <button class="mini-btn is-danger" data-delete="${esc(c.id)}" title="Delete">${icon('trash')}</button>
      </span>
    </div>`).join('');

  container.querySelectorAll('[data-open]').forEach((el) => {
    el.addEventListener('click', () => onOpen?.(el.dataset.open));
  });
  container.querySelectorAll('[data-pin]').forEach((el) => {
    el.addEventListener('click', (e) => { e.stopPropagation(); onPin?.(el.dataset.pin, el.getAttribute('aria-pressed') !== 'true'); });
  });
  container.querySelectorAll('[data-archive]').forEach((el) => {
    el.addEventListener('click', (e) => { e.stopPropagation(); onArchive?.(el.dataset.archive, !archived); });
  });
  container.querySelectorAll('[data-export]').forEach((el) => {
    el.addEventListener('click', (e) => { e.stopPropagation(); onExport?.(el.dataset.export); });
  });
  container.querySelectorAll('[data-delete]').forEach((el) => {
    el.addEventListener('click', (e) => { e.stopPropagation(); onDelete?.(el.dataset.delete); });
  });
}

export function renderProjectChips(container, { projects = [], filter = 'all', onSelect } = {}) {
  const chips = [{ id: 'all', label: 'All' }, { id: 'unfiled', label: 'Unfiled' },
    ...projects.map((p) => ({ id: p.id, label: `${p.name} (${p.conversation_count || 0})` }))];
  container.innerHTML = chips.map((c) => `
    <button class="chip ${c.id === filter ? 'is-active' : ''}" data-project="${esc(c.id)}"
            aria-pressed="${c.id === filter}">${esc(c.label)}</button>`).join('');
  container.querySelectorAll('[data-project]').forEach((el) => {
    el.addEventListener('click', () => onSelect?.(el.dataset.project));
  });
}

export function renderConversationActions(container, {
  hasConversation = false, pinned = false, archived = false,
  onPin, onArchive, onExport, onDelete,
} = {}) {
  container.innerHTML = `
    <button class="icon-btn" data-act="pin" ${hasConversation ? '' : 'disabled'}
            aria-pressed="${pinned}" title="${pinned ? 'Unpin conversation' : 'Pin conversation'}">${icon('pin')}</button>
    <button class="icon-btn" data-act="archive" ${hasConversation ? '' : 'disabled'}
            title="${archived ? 'Restore conversation' : 'Archive conversation'}">${icon('archive')}</button>
    <button class="icon-btn" data-act="export" ${hasConversation ? '' : 'disabled'}
            title="Export as Markdown">${icon('download')}</button>
    <button class="icon-btn" data-act="delete" ${hasConversation ? '' : 'disabled'}
            title="Delete conversation">${icon('trash')}</button>`;
  container.querySelector('[data-act="pin"]').addEventListener('click', () => onPin?.());
  container.querySelector('[data-act="archive"]').addEventListener('click', () => onArchive?.());
  container.querySelector('[data-act="export"]').addEventListener('click', () => onExport?.());
  container.querySelector('[data-act="delete"]').addEventListener('click', () => onDelete?.());
}

/* ----------------------------------------------------------- attachments */
export function renderAttachments(container, items, { onRemove } = {}) {
  if (!items.length) {
    container.hidden = true;
    container.innerHTML = '';
    return;
  }
  container.hidden = false;
  container.innerHTML = items.map((a) => `
    <span class="attach-chip ${a.pending ? 'is-pending' : ''}">
      ${icon('paperclip')}
      <span class="attach-name">${esc(a.filename)}</span>
      ${a.error ? `<span class="attach-error">${esc(a.error)}</span>` : ''}
      <button type="button" class="mini-btn" data-remove="${esc(a.id || a.filename)}"
              aria-label="Remove ${esc(a.filename)}">${icon('close')}</button>
    </span>`).join('');
  container.querySelectorAll('[data-remove]').forEach((el) => {
    el.addEventListener('click', () => onRemove?.(el.dataset.remove));
  });
}

/* -------------------------------------------------------------- projects */
export function projectDialog({ project = null, onSave } = {}) {
  const editing = Boolean(project);
  const { root, close } = mountDialog(`
    <div class="modal-backdrop">
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
        <h2 id="modalTitle">${editing ? 'Edit project' : 'New project'}</h2>
        <p>A project groups conversations and gives every one of them the same instructions.</p>
        <label for="projName">Name</label>
        <input id="projName" type="text" maxlength="80" autocomplete="off"
               value="${editing ? esc(project.name) : ''}" placeholder="e.g. Thesis research" />
        <label for="projDesc">Description (optional)</label>
        <input id="projDesc" type="text" maxlength="200" autocomplete="off"
               value="${editing ? esc(project.description || '') : ''}" placeholder="What this workspace is for" />
        <label for="projInstr">Instructions for Tolesh</label>
        <textarea id="projInstr" rows="4" placeholder="e.g. Answer with sources. Keep replies under 200 words.">${editing ? esc(project.instructions || '') : ''}</textarea>
        <div class="modal-actions">
          <button class="btn" data-cancel>Cancel</button>
          <button class="btn btn-primary" data-save>${editing ? 'Save' : 'Create'}</button>
        </div>
      </div>
    </div>`);
  root.querySelector('[data-cancel]').addEventListener('click', close);
  root.querySelector('[data-save]').addEventListener('click', () => {
    const name = root.querySelector('#projName').value.trim();
    if (!name) { toast('A project needs a name.', 'error'); return; }
    const payload = {
      name,
      description: root.querySelector('#projDesc').value.trim(),
      instructions: root.querySelector('#projInstr').value.trim(),
    };
    close();
    onSave?.(payload);
  });
  root.querySelector('#projName').focus();
}

export function pickProjectDialog({ projects = [], current = null, onPick } = {}) {
  const rows = [
    `<button class="pick-row ${!current ? 'is-active' : ''}" data-pick="">
       <span>No project</span><span class="pick-note">unfiled</span></button>`,
    ...projects.map((p) => `
      <button class="pick-row ${p.id === current ? 'is-active' : ''}" data-pick="${esc(p.id)}">
        <span>${esc(p.name)}</span>
        <span class="pick-note">${p.conversation_count || 0} chats</span></button>`),
  ].join('');
  const { root, close } = mountDialog(`
    <div class="modal-backdrop">
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
        <h2 id="modalTitle">Move to project</h2>
        <div class="pick-list">${rows}</div>
        <div class="modal-actions"><button class="btn" data-cancel>Cancel</button></div>
      </div>
    </div>`);
  root.querySelector('[data-cancel]').addEventListener('click', close);
  root.querySelectorAll('[data-pick]').forEach((el) => {
    el.addEventListener('click', () => { close(); onPick?.(el.dataset.pick || null); });
  });
}
