/* Reusable, self-contained Tolesh UI components.
   Every dynamic string is escaped. Message bodies are written with textContent.
   See docs/COMPONENT-LIBRARY.md for contracts. */

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

export function icon(id, cls = '') {
  return `<svg class="icon ${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;
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
    ['Providers ready', String((data.providers_configured || []).length)],
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

export function renderUsage(container, usage) {
  const nf = new Intl.NumberFormat();
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
  container.innerHTML = tools.map((t) => `
    <div class="tool-row" data-tool="${esc(t.name)}">
      <span class="tname">${esc(t.name)}</span>
      <span class="risk-tag risk-${esc(t.risk)}">${esc(t.risk)}</span>
      <button class="invoke" data-invoke="${esc(t.name)}">${icon('arrow')}</button>
    </div>`).join('');
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

/** Provider configuration dialog: endpoint, key, enabled state, and a real
 *  connection test. The backend never echoes a stored key, so an empty key
 *  field means "keep the secret that is already saved". */
export function providerDialog(provider, { onSave, onTest } = {}) {
  const hasKey = provider.api_key === '***' || provider.configured;
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
        <p class="modal-note" id="provTestResult" role="status" aria-live="polite"></p>
        <div class="modal-actions">
          <button class="btn" data-cancel>Cancel</button>
          <button class="btn" data-test>Test connection</button>
          <button class="btn btn-primary" data-save>Save</button>
        </div>
      </div>
    </div>`);

  const result = root.querySelector('#provTestResult');
  const patchFrom = () => {
    const patch = {
      base_url: root.querySelector('#provBaseUrl').value.trim(),
      enabled: root.querySelector('#provEnabled').checked,
    };
    const key = root.querySelector('#provApiKey').value;
    if (key) patch.api_key = key;
    return patch;
  };

  root.querySelector('[data-cancel]').addEventListener('click', close);
  root.querySelector('[data-save]').addEventListener('click', () => {
    const patch = patchFrom();
    close();
    if (typeof onSave === 'function') onSave(patch);
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
        result.textContent = `${r.status}: ${r.detail || 'no answer'}`;
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
      done(args);
      close();
    });
    (root.querySelector('#toolPath') || root.querySelector('#toolName') || root.querySelector('#toolCmd'))?.focus();
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
