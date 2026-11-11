/* Tolesh workspace drawer.

   The chat surface stays the front door; this is the workshop behind it:
   conversation tools, agents, MCP servers, external connections, files,
   background tasks, saved assistants and privacy settings.

   Design rules kept from the rest of the app:
   - every control states what it will do, including when it is unavailable;
   - destructive actions ask first;
   - nothing claims a capability the server has not confirmed. */

import * as api from './api.js';
import { confirmDialog, esc, toast } from './components.js';

const TABS = [
  ['conversation', 'Conversation'],
  ['memory', 'Memory'],
  ['agents', 'Agents'],
  ['mcp', 'MCP'],
  ['connections', 'Connections'],
  ['files', 'Files'],
  ['tasks', 'Tasks'],
  ['assistants', 'Assistants'],
  ['settings', 'Settings'],
];

let drawer = null;
let body = null;
let tabButtons = new Map();
let activeTab = 'conversation';
let agentAbort = null;

/* --------------------------------------------------------------- helpers */
function h(html) {
  const template = document.createElement('template');
  template.innerHTML = html.trim();
  return template.content.firstElementChild;
}

function badge(text, kind = '') {
  return `<span class="ws-badge ${kind ? `is-${kind}` : ''}">${esc(text)}</span>`;
}

function when(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts * 1000).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch (_) {
    return '—';
  }
}

function bytes(n) {
  if (!n && n !== 0) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function fail(err) {
  toast(err && err.message ? err.message : String(err), 'error');
}

async function guard(fn) {
  try {
    return await fn();
  } catch (err) {
    fail(err);
    return null;
  }
}

function conversationId() {
  return window.__toleshConversationId || null;
}

/* ------------------------------------------------------------------ shell */
export function initWorkspace() {
  if (drawer) return;

  const rail = document.querySelector('.rail-nav');
  if (rail) {
    const button = h(`
      <button class="rail-item ws-rail-item" id="workspaceRailBtn" type="button"
              title="Workspace: agents, servers, connections, files, tasks">
        <svg class="icon" aria-hidden="true"><use href="#i-layers" /></svg>
        <span class="rail-label">Workspace</span>
      </button>`);
    button.addEventListener('click', () => openWorkspace());
    rail.appendChild(button);
  }

  const scrim = h('<div class="ws-scrim" id="wsScrim" hidden></div>');
  drawer = h(`
    <aside class="ws-drawer" id="wsDrawer" role="dialog" aria-modal="true"
           aria-labelledby="wsTitle" aria-hidden="true">
      <div class="ws-head">
        <div>
          <p class="ws-eyebrow">Workspace</p>
          <h2 id="wsTitle">What Tolesh can do</h2>
        </div>
        <button class="icon-btn" id="wsClose" type="button" aria-label="Close workspace">
          <svg class="icon" aria-hidden="true"><use href="#i-close" /></svg>
        </button>
      </div>
      <div class="ws-tabs" role="tablist" aria-label="Workspace sections"></div>
      <div class="ws-body" id="wsBody"></div>
    </aside>`);
  body = drawer.querySelector('#wsBody');

  const tabs = drawer.querySelector('.ws-tabs');
  for (const [id, label] of TABS) {
    const button = h(`<button class="ws-tab" role="tab" type="button"
      id="ws-tab-${id}" aria-controls="ws-panel-${id}"
      aria-selected="${id === activeTab}">${label}</button>`);
    button.addEventListener('click', () => selectTab(id));
    // The tablist pattern: arrows move between tabs, Home/End jump to the ends.
    button.addEventListener('keydown', (event) => {
      const order = TABS.map(([key]) => key);
      const index = order.indexOf(id);
      let next = null;
      if (event.key === 'ArrowRight') next = order[(index + 1) % order.length];
      if (event.key === 'ArrowLeft') next = order[(index - 1 + order.length) % order.length];
      if (event.key === 'Home') next = order[0];
      if (event.key === 'End') next = order[order.length - 1];
      if (!next) return;
      event.preventDefault();
      selectTab(next);
      tabButtons.get(next)?.focus();
    });
    tabs.appendChild(button);
    tabButtons.set(id, button);
  }

  document.body.append(scrim, drawer);
  scrim.addEventListener('click', closeWorkspace);
  drawer.querySelector('#wsClose').addEventListener('click', closeWorkspace);
  document.addEventListener('keydown', (event) => {
    // Escape closes the innermost layer first: the image viewer sits above the
    // drawer and handles its own Escape.
    if (document.querySelector('.ws-lightbox')) return;
    if (!drawer.classList.contains('is-open')) return;
    if (event.key === 'Escape') {
      closeWorkspace();
      return;
    }
    if (event.key !== 'Tab') return;
    // The drawer declares aria-modal, so focus must not wander behind it.
    const focusable = [...drawer.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )].filter((node) => !node.disabled && node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  window.addEventListener('tolesh:conversation', () => {
    if (activeTab === 'conversation' && drawer.classList.contains('is-open')) {
      renderConversation();
    }
  });

  selectTab(activeTab);
}

export function openWorkspace(tab) {
  initWorkspace();
  drawer.classList.add('is-open');
  drawer.setAttribute('aria-hidden', 'false');
  document.getElementById('wsScrim').hidden = false;
  document.getElementById('wsScrim').classList.add('is-open');
  document.body.style.overflow = 'hidden';
  // Re-render on every open, not only when a tab is named. The panel is built
  // once at page load, so without this the drawer shows the state of the page
  // as it was before the user's first conversation — and stale data everywhere
  // else. Opening the drawer is a request for current data.
  selectTab(tab || activeTab);
  drawer.querySelector('#wsClose').focus();
}

export function closeWorkspace() {
  if (!drawer) return;
  drawer.classList.remove('is-open');
  drawer.setAttribute('aria-hidden', 'true');
  const scrim = document.getElementById('wsScrim');
  scrim.classList.remove('is-open');
  scrim.hidden = true;
  document.body.style.overflow = '';
  if (agentAbort) {
    agentAbort.abort();
    agentAbort = null;
  }
  const rail = document.getElementById('workspaceRailBtn');
  if (rail) rail.focus();
}

function selectTab(id) {
  activeTab = id;
  for (const [key, button] of tabButtons) {
    button.setAttribute('aria-selected', String(key === id));
  }
  body.innerHTML = '';
  const render = {
    conversation: renderConversation,
    memory: renderMemory,
    agents: renderAgents,
    mcp: renderMcp,
    connections: renderConnections,
    files: renderFiles,
    tasks: renderTasks,
    assistants: renderAssistants,
    settings: renderSettings,
  }[id];
  if (render) render();
}

function panel(title, note) {
  // The tab declares aria-controls="ws-panel-<id>"; the panel has to answer to
  // that id or the relationship is decorative.
  const wrap = h(`<section class="ws-panel" role="tabpanel"
    id="ws-panel-${activeTab}" aria-labelledby="ws-tab-${activeTab}"
    tabindex="0">
    <h3>${esc(title)}</h3>
    ${note ? `<p class="ws-note">${esc(note)}</p>` : ''}
    <div class="ws-panel-body"></div>
  </section>`);
  body.appendChild(wrap);
  return wrap.querySelector('.ws-panel-body');
}

/* ---------------------------------------------------------- conversation */
async function renderConversation() {
  const host = panel('This conversation',
    'Edit a question, branch the thread, or answer the last question again.');
  const cid = conversationId();
  if (!cid) {
    host.innerHTML = '<div class="ws-empty">Open or start a conversation first.</div>';
    return;
  }
  host.innerHTML = '<div class="ws-empty">Loading…</div>';
  const [data, meta] = await guard(() => Promise.all([
    api.get(`/api/conversations/${encodeURIComponent(cid)}/messages`),
    api.get(`/api/conversations/${encodeURIComponent(cid)}`),
  ])) || [{ messages: [] }, { conversation: {} }];

  host.innerHTML = '';
  const head = h(`<div class="ws-row" style="margin-bottom:.8rem">
      <div class="ws-card-sub">${esc(meta.conversation?.title || 'Untitled')} ·
        ${data.messages.length} messages</div>
      <span style="flex:1"></span>
      <button class="ws-btn" id="wsRegen" type="button">Answer again</button>
      <button class="ws-btn" id="wsExport" type="button">Export Markdown</button>
      <button class="ws-btn" id="wsShare" type="button">Share link</button>
    </div>`);
  host.appendChild(head);

  head.querySelector('#wsRegen').addEventListener('click', () => regenerate(cid, head));
  head.querySelector('#wsExport').addEventListener('click', () => {
    api.download(`/api/conversations/${encodeURIComponent(cid)}/export?format=markdown`)
      .catch(fail);
  });
  head.querySelector('#wsShare').addEventListener('click', () => shareDialog(cid));

  if (!data.messages.length) {
    host.appendChild(h('<div class="ws-empty">No messages yet.</div>'));
  }
  for (const message of data.messages) {
    const block = h(`<div class="ws-msg is-${message.role === 'user' ? 'user' : 'assistant'}">
      <div class="ws-msg-role">${message.role === 'user' ? 'You' : 'Tolesh'}
        ${message.model ? `· ${esc(message.model)}` : ''}</div>
      <p class="ws-msg-text">${esc(message.content)}</p>
      <div class="ws-row">
        <button class="ws-btn" data-act="branch" type="button">Branch here</button>
        ${message.role === 'user' ? '<button class="ws-btn" data-act="edit" type="button">Edit</button>' : ''}
        <button class="ws-btn is-danger" data-act="delete" type="button">Delete</button>
      </div>
    </div>`);
    block.querySelector('[data-act="branch"]').addEventListener('click', async () => {
      const res = await guard(() => api.post(
        `/api/conversations/${encodeURIComponent(cid)}/branch`,
        { message_id: message.id }));
      if (res) {
        toast(`Branched into “${res.conversation.title}”. Open it from History.`, 'success');
        renderConversation();
      }
    });
    const edit = block.querySelector('[data-act="edit"]');
    if (edit) {
      edit.addEventListener('click', async () => {
        const next = window.prompt('Edit this message. Everything after it is removed.',
          message.content);
        if (next === null || !next.trim()) return;
        const res = await guard(() => api.patch(
          `/api/conversations/${encodeURIComponent(cid)}/messages/${message.id}`,
          { content: next.trim() }));
        if (res) {
          toast('Message updated. Send it again to get a fresh answer.', 'success');
          renderConversation();
        }
      });
    }
    block.querySelector('[data-act="delete"]').addEventListener('click', async () => {
      const res = await guard(() => api.del(
        `/api/conversations/${encodeURIComponent(cid)}/messages/${message.id}`));
      if (res) renderConversation();
    });
    host.appendChild(block);
  }

  const shares = await guard(() => api.get('/api/shares'));
  if (shares && shares.shares.length) {
    const list = h('<div><h3>Share links</h3><ul class="ws-list"></ul></div>');
    for (const share of shares.shares.filter((s) => s.conversation_id === cid)) {
      const item = h(`<li><div><div class="ws-card-sub">${esc(share.url)}</div>
        <div class="ws-card-sub">${share.active ? 'active' : 'revoked'} ·
        ${share.views} views</div></div>
        <button class="ws-btn is-danger" type="button">Revoke</button></li>`);
      item.querySelector('button').addEventListener('click', async () => {
        await guard(() => api.del(`/api/shares/${encodeURIComponent(share.token)}`));
        renderConversation();
      });
      list.querySelector('ul').appendChild(item);
    }
    host.appendChild(list);
  }
}

async function regenerate(cid, head) {
  const button = head.querySelector('#wsRegen');
  button.disabled = true;
  button.textContent = 'Answering…';
  let text = '';
  try {
    await api.streamPost(`/api/conversations/${encodeURIComponent(cid)}/regenerate`, undefined,
      (event) => {
        if (event.type === 'token') text += String(event.data ?? '');
        if (event.type === 'error') toast(event.data?.message || 'The model failed.', 'error');
      });
    if (text) toast('A new answer replaced the previous one.', 'success');
  } catch (err) {
    fail(err);
  } finally {
    renderConversation();
  }
}

async function shareDialog(cid) {
  const config = await guard(() => api.get('/api/shares'));
  if (!config) return;
  if (!config.enabled) {
    toast('Sharing is off. Turn on “Share links” in Settings first.', 'error');
    selectTab('settings');
    return;
  }
  confirmDialog({
    title: 'Create a public link?',
    body: 'Anyone with the link can read this one conversation. No other chat, '
      + 'file or setting is included, and you can revoke it at any time.',
    confirmLabel: 'Create link',
    onConfirm: async () => {
      const res = await guard(() => api.post(
        `/api/conversations/${encodeURIComponent(cid)}/share`, {}));
      if (res) {
        const url = `${location.origin}${res.share.url}`;
        try {
          await navigator.clipboard.writeText(url);
          toast('Link copied to the clipboard.', 'success');
        } catch (_) {
          window.prompt('Copy this link:', url);
        }
        renderConversation();
      }
    },
  });
}

/* ----------------------------------------------------------------- agents */
async function renderAgents() {
  const host = panel('Agents',
    'Give a task. Tolesh plans, calls tools one at a time and answers. '
    + 'Anything that changes data outside Tolesh stops for your approval.');
  host.innerHTML = `
    <div class="ws-field"><span>Task</span>
      <textarea id="wsGoal" placeholder="For example: profile the sales.csv I uploaded and draw a bar chart of revenue by region."></textarea></div>
    <div class="ws-row">
      <button class="ws-btn is-primary" id="wsRun" type="button">Run agent</button>
      <button class="ws-btn" id="wsCancel" type="button" disabled>Stop</button>
      <span class="ws-card-sub" id="wsRunState"></span>
    </div>
    <pre class="ws-log" id="wsLog" hidden></pre>
    <h3>Recent runs</h3>
    <div id="wsRuns"><div class="ws-empty">Loading…</div></div>`;

  const log = host.querySelector('#wsLog');
  const runButton = host.querySelector('#wsRun');
  const cancelButton = host.querySelector('#wsCancel');
  const state = host.querySelector('#wsRunState');

  const append = (line) => {
    log.hidden = false;
    log.textContent += `${line}\n`;
    log.scrollTop = log.scrollHeight;
  };

  runButton.addEventListener('click', async () => {
    const goal = host.querySelector('#wsGoal').value.trim();
    if (!goal) {
      toast('Describe the task first.', 'error');
      return;
    }
    log.textContent = '';
    runButton.disabled = true;
    cancelButton.disabled = false;
    state.textContent = 'Starting…';
    agentAbort = new AbortController();
    let runId = null;
    try {
      const created = await api.post('/api/agents/runs', { goal });
      runId = created.run.id;
      state.textContent = `Run ${runId}`;
      await api.streamPost(`/api/agents/runs/${runId}/run`, undefined, (event) => {
        const data = event.data || {};
        if (event.type === 'step') append(`— step ${data.step} —`);
        if (event.type === 'thought' && data.text) append(`think: ${data.text}`);
        if (event.type === 'tool_call') {
          append(`call ${data.tool}(${JSON.stringify(data.args || {})})`);
        }
        if (event.type === 'tool_result') {
          append(`${data.ok ? 'ok' : 'failed'} ${data.tool}: ${(data.text || '').slice(0, 400)}`);
        }
        if (event.type === 'approval_required') {
          state.textContent = 'Waiting for approval';
          append('needs approval — open Approvals in the context panel');
        }
        if (event.type === 'final') {
          state.textContent = `Done in ${data.steps} steps`;
          append(`final: ${data.answer}`);
        }
        if (event.type === 'error') {
          state.textContent = 'Stopped';
          append(`error: ${data.message}`);
        }
        if (event.type === 'cancelled') {
          state.textContent = 'Cancelled';
          append('cancelled');
        }
      }, agentAbort.signal);
    } catch (err) {
      if (err.name !== 'AbortError') fail(err);
    } finally {
      runButton.disabled = false;
      cancelButton.disabled = true;
      agentAbort = null;
      loadRuns(host.querySelector('#wsRuns'));
    }
  });

  cancelButton.addEventListener('click', async () => {
    if (agentAbort) agentAbort.abort();
    const text = state.textContent.replace('Run ', '').trim();
    if (text.startsWith('run-')) {
      await guard(() => api.post(`/api/agents/runs/${text}/cancel`, {}));
    }
  });

  loadRuns(host.querySelector('#wsRuns'));
}

async function loadRuns(container) {
  const data = await guard(() => api.get('/api/agents/runs'));
  if (!data) return;
  container.innerHTML = '';
  if (!data.runs.length) {
    container.appendChild(h('<div class="ws-empty">No runs yet.</div>'));
    return;
  }
  const list = h('<ul class="ws-list"></ul>');
  for (const run of data.runs.slice(0, 12)) {
    const kind = run.status === 'done' ? 'ok'
      : run.status === 'failed' || run.status === 'error' ? 'bad'
        : run.status === 'waiting_approval' ? 'warn' : '';
    const item = h(`<li><div>
        <div class="ws-card-title">${esc(run.goal.slice(0, 90))}</div>
        <div class="ws-card-sub">${when(run.updated_at)} · ${run.steps || 0} steps
          ${run.result ? `· ${esc(String(run.result).slice(0, 80))}` : ''}</div>
      </div><div class="ws-row">${badge(run.status, kind)}
        <button class="ws-btn is-danger" type="button">Delete</button></div></li>`);
    item.querySelector('button').addEventListener('click', async () => {
      await guard(() => api.del(`/api/agents/runs/${run.id}`));
      loadRuns(container);
    });
    list.appendChild(item);
  }
  container.appendChild(list);
}

/* -------------------------------------------------------------------- MCP */
async function renderMcp() {
  const host = panel('MCP servers',
    'Connect tool servers over stdio or HTTP. Each tool is shown with the '
    + 'permission it will use before it runs.');
  host.innerHTML = `
    <form id="wsMcpForm" class="ws-card">
      <div class="ws-row">
        <label class="ws-field"><span>Name</span><input name="name" required placeholder="github"></label>
        <label class="ws-field"><span>Transport</span>
          <select name="transport">
            <option value="stdio">stdio (local process)</option>
            <option value="http">http (streamable)</option>
          </select></label>
      </div>
      <div id="wsMcpStdio">
        <div class="ws-row">
          <label class="ws-field"><span>Command</span><input name="command" placeholder="npx"></label>
          <label class="ws-field"><span>Arguments (space separated)</span><input name="args" placeholder="-y @modelcontextprotocol/server-filesystem /tmp"></label>
        </div>
      </div>
      <div id="wsMcpHttp" hidden>
        <label class="ws-field"><span>URL</span><input name="url" placeholder="https://example.test/mcp"></label>
        <label class="ws-field"><span>Authorization header (optional)</span><input name="auth" placeholder="Bearer …" autocomplete="off"></label>
      </div>
      <label class="ws-field"><span>Default tool permission</span>
        <select name="default_policy">
          <option value="allow">allow</option>
          <option value="ask">ask</option>
          <option value="deny">deny</option>
        </select></label>
      <div class="ws-row"><button class="ws-btn is-primary" type="submit">Add server</button>
        <span class="ws-card-sub">Secrets are stored 0600 on this machine and never shown again.</span></div>
    </form>
    <h3>Configured</h3>
    <div id="wsMcpList"><div class="ws-empty">Loading…</div></div>`;

  const form = host.querySelector('#wsMcpForm');
  form.transport.addEventListener('change', () => {
    const isHttp = form.transport.value === 'http';
    host.querySelector('#wsMcpHttp').hidden = !isHttp;
    host.querySelector('#wsMcpStdio').hidden = isHttp;
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const body = {
      name: form.name.value.trim(),
      transport: form.transport.value,
      default_policy: form.default_policy.value,
    };
    if (body.transport === 'stdio') {
      body.command = form.command.value.trim();
      body.args = form.args.value.trim() ? form.args.value.trim().split(/\s+/) : [];
    } else {
      body.url = form.url.value.trim();
      if (form.auth.value.trim()) body.headers = { Authorization: form.auth.value.trim() };
    }
    const res = await guard(() => api.post('/api/mcp/servers', body));
    if (res) {
      toast('Server added. Testing the connection…', 'success');
      form.reset();
      host.querySelector('#wsMcpHttp').hidden = true;
      host.querySelector('#wsMcpStdio').hidden = false;
      await guard(() => api.post(`/api/mcp/servers/${res.server.name}/test`, {}));
      loadMcpList(host.querySelector('#wsMcpList'));
    }
  });

  loadMcpList(host.querySelector('#wsMcpList'));
}

async function loadMcpList(container) {
  const data = await guard(() => api.get('/api/mcp/servers'));
  if (!data) return;
  container.innerHTML = '';
  if (!data.servers.length) {
    container.appendChild(h('<div class="ws-empty">No servers yet. Add one above.</div>'));
    return;
  }
  for (const server of data.servers) {
    const state = server.status?.state || 'idle';
    const kind = state === 'connected' ? 'ok' : state === 'error' ? 'bad' : '';
    const card = h(`<div class="ws-card">
      <div class="ws-card-head">
        <div><div class="ws-card-title">${esc(server.name)}</div>
          <div class="ws-card-sub">${esc(server.transport)} ·
            ${server.transport === 'stdio' ? esc(server.command) : esc(server.url)}</div></div>
        <div class="ws-row">${badge(state, kind)}
          <button class="ws-btn" data-act="test" type="button">Test</button>
          <button class="ws-btn" data-act="tools" type="button">Tools</button>
          <button class="ws-btn is-danger" data-act="delete" type="button">Remove</button></div>
      </div>
      <div class="ws-card-sub">${server.status?.tools || 0} tools · policy
        <strong>${esc(server.default_policy)}</strong>
        ${server.status?.error ? `· ${esc(server.status.error)}` : ''}</div>
      <div data-tools></div>
    </div>`);

    card.querySelector('[data-act="test"]').addEventListener('click', async () => {
      // Testing replaces this whole card when it finishes, so every button in
      // it is disabled first — a click on a node that is about to be replaced
      // would silently do nothing.
      const buttons = [...card.querySelectorAll('button')];
      for (const button of buttons) button.disabled = true;
      const result = await guard(() => api.post(`/api/mcp/servers/${server.name}/test`, {}));
      for (const button of buttons) button.disabled = false;
      if (result) {
        toast(result.ok
          ? `${server.name}: ${result.tools} tools in ${result.duration_ms} ms`
          : `${server.name}: ${result.error}`, result.ok ? 'success' : 'error');
        loadMcpList(container);
      }
    });
    card.querySelector('[data-act="delete"]').addEventListener('click', () => {
      confirmDialog({
        title: `Remove ${server.name}?`,
        body: 'The configuration and its stored headers are deleted. Tools from '
          + 'this server stop being available.',
        confirmLabel: 'Remove',
        danger: true,
        onConfirm: async () => {
          await guard(() => api.del(`/api/mcp/servers/${encodeURIComponent(server.name)}`));
          loadMcpList(container);
        },
      });
    });
    card.querySelector('[data-act="tools"]').addEventListener('click', async (event) => {
      const target = card.querySelector('[data-tools]');
      if (target.innerHTML) {
        target.innerHTML = '';
        return;
      }
      event.target.disabled = true;
      const info = await guard(() => api.get(`/api/mcp/servers/${encodeURIComponent(server.name)}/tools`));
      event.target.disabled = false;
      if (!info) return;
      if (!info.tools.length) {
        target.innerHTML = '<div class="ws-empty">This server exposes no tools.</div>';
        return;
      }
      target.innerHTML = '<div class="ws-divider"></div>';
      for (const tool of info.tools) {
        const row = h(`<div class="ws-row" style="margin:.35rem 0">
          <div style="flex:1;min-width:0"><div class="ws-card-title">${esc(tool.name)}</div>
            <div class="ws-card-sub">${esc((tool.description || '').slice(0, 140))}</div></div>
          ${badge(tool.permission, tool.permission === 'deny' ? 'bad' : tool.permission === 'ask' ? 'warn' : 'ok')}
          <button class="ws-btn" type="button">Run</button></div>`);
        row.querySelector('button').addEventListener('click', async () => {
          const raw = window.prompt(`Arguments for ${tool.name} as JSON`, '{}');
          if (raw === null) return;
          let args = {};
          try {
            args = JSON.parse(raw || '{}');
          } catch (_) {
            toast('That is not valid JSON.', 'error');
            return;
          }
          const result = await guard(() => api.post('/api/mcp/invoke', {
            server: server.name, tool: tool.name, args,
          }));
          if (!result) return;
          if (result.status === 'pending-approval') {
            toast('Approval requested. Approve it in the context panel.', 'info');
          } else if (result.status === 'ok') {
            window.prompt(`${tool.name} returned:`, (result.text || '').slice(0, 2000));
          } else {
            toast(result.message || 'The tool failed.', 'error');
          }
        });
        target.appendChild(row);
      }
    });
    container.appendChild(card);
  }
}

/* ------------------------------------------------------------ connections */
async function renderConnections() {
  const host = panel('Connections',
    'Link an external account. Tokens are encrypted on this machine and never '
    + 'shown again; writes ask for approval.');
  host.innerHTML = '<div class="ws-empty">Loading…</div>';
  const data = await guard(() => api.get('/api/connectors'));
  if (!data) return;
  host.innerHTML = '';

  if (data.connections.length) {
    const list = h('<div><h3>Connected</h3><div data-list></div></div>');
    for (const connection of data.connections) {
      const kind = connection.status === 'connected' ? 'ok'
        : connection.status === 'expired' ? 'warn' : 'bad';
      const card = h(`<div class="ws-card">
        <div class="ws-card-head">
          <div><div class="ws-card-title">${esc(connection.label)}</div>
            <div class="ws-card-sub">${esc(connection.provider)} ·
              ${esc(connection.account || 'account unknown')} ·
              ${connection.has_refresh ? 'refreshable' : 'static token'}</div></div>
          <div class="ws-row">${badge(connection.status, kind)}
            <button class="ws-btn" data-act="test" type="button">Test</button>
            <button class="ws-btn is-danger" data-act="delete" type="button">Disconnect</button></div>
        </div></div>`);
      card.querySelector('[data-act="test"]').addEventListener('click', async () => {
        const result = await guard(() => api.post(
          `/api/connectors/${encodeURIComponent(connection.id)}/test`, {}));
        if (result) toast(`${connection.label}: connected as ${result.account || 'unknown'}`,
          'success');
      });
      card.querySelector('[data-act="delete"]').addEventListener('click', () => {
        confirmDialog({
          title: `Disconnect ${connection.label}?`,
          body: 'The stored token is deleted. Nothing else changes.',
          confirmLabel: 'Disconnect', danger: true,
          onConfirm: async () => {
            await guard(() => api.del(`/api/connectors/${encodeURIComponent(connection.id)}`));
            renderConnections();
          },
        });
      });
      list.querySelector('[data-list]').appendChild(card);
    }
    host.appendChild(list);
  }

  const catalogue = h('<div><h3>Available providers</h3><div data-providers></div></div>');
  for (const provider of data.providers) {
    const card = h(`<div class="ws-card">
      <div class="ws-card-head">
        <div><div class="ws-card-title">${esc(provider.label)}</div>
          <div class="ws-card-sub">${esc(provider.auth)} ·
            ${provider.actions.length} actions · ${provider.scopes.length} scopes</div></div>
        <div class="ws-row">${badge(provider.connections ? `${provider.connections} linked` : 'not linked',
          provider.connections ? 'ok' : '')}</div>
      </div>
      <div data-body></div>
    </div>`);
    const target = card.querySelector('[data-body]');
    if (provider.supports_token) {
      const form = h(`<div class="ws-inline-form" style="margin-top:.4rem">
        <input name="token" type="password" placeholder="Paste a personal access token"
               autocomplete="off" />
        <button class="ws-btn" type="button">Connect</button></div>`);
      form.querySelector('button').addEventListener('click', async () => {
        const token = form.querySelector('input').value.trim();
        if (!token) return;
        const res = await guard(() => api.post('/api/connectors/token', {
          provider: provider.id, token,
        }));
        if (res) {
          toast(`${provider.label} connected as ${res.connection.account || 'account'}.`,
            'success');
          renderConnections();
        }
      });
      target.appendChild(form);
    }
    if (provider.supports_oauth) {
      const oauth = h(`<details style="margin-top:.45rem">
        <summary class="ws-card-sub">Connect with OAuth</summary>
        <div class="ws-row" style="margin-top:.5rem">
          <label class="ws-field"><span>Client id</span><input name="client_id" autocomplete="off"></label>
          <label class="ws-field"><span>Client secret (optional)</span>
            <input name="client_secret" type="password" autocomplete="off"></label>
        </div>
        <label class="ws-field"><span>Redirect URI registered with ${esc(provider.label)}</span>
          <input name="redirect" value="${esc(location.origin)}/api/connectors/oauth/callback"></label>
        <button class="ws-btn" type="button">Open sign-in</button>
        <p class="ws-card-sub">Create an app with the provider, allow this redirect URI,
          then finish the sign-in in the tab that opens.</p>
      </details>`);
      oauth.querySelector('button').addEventListener('click', async () => {
        const res = await guard(() => api.post('/api/connectors/oauth/start', {
          provider: provider.id,
          client_id: oauth.querySelector('[name=client_id]').value.trim(),
          client_secret: oauth.querySelector('[name=client_secret]').value.trim(),
          redirect_uri: oauth.querySelector('[name=redirect]').value.trim(),
        }));
        if (res) window.open(res.authorize_url, '_blank', 'noopener');
      });
      target.appendChild(oauth);
    }
    catalogue.querySelector('[data-providers]').appendChild(card);
  }
  host.appendChild(catalogue);
}

/* ------------------------------------------------------------------ files */
async function renderFiles() {
  const host = panel('Files and analysis',
    'Upload a table, profile it, and turn it into a chart or a report you can download.');
  host.innerHTML = `
    <div class="ws-row">
      <input type="file" id="wsFile" multiple />
      <button class="ws-btn is-primary" id="wsUpload" type="button">Upload</button>
      <span class="ws-card-sub" id="wsFileState"></span>
    </div>
    <h3>Uploaded files</h3>
    <div id="wsAttachments"><div class="ws-empty">Loading…</div></div>
    <h3>Chart</h3>
    <div class="ws-card">
      <div class="ws-row">
        <label class="ws-field"><span>File</span><select id="wsChartFile"></select></label>
        <label class="ws-field"><span>Type</span>
          <select id="wsChartType">
            <option>bar</option><option>line</option><option>scatter</option>
            <option>hist</option><option>pie</option>
          </select></label>
      </div>
      <div class="ws-row">
        <label class="ws-field"><span>X column</span><input id="wsChartX" placeholder="region"></label>
        <label class="ws-field"><span>Y column (optional)</span><input id="wsChartY" placeholder="amount"></label>
      </div>
      <label class="ws-field"><span>Title</span><input id="wsChartTitle" placeholder="Revenue by region"></label>
      <div class="ws-row">
        <button class="ws-btn" id="wsAnalyse" type="button">Profile data</button>
        <button class="ws-btn is-primary" id="wsChart" type="button">Create chart</button>
      </div>
      <pre class="ws-log" id="wsProfile" hidden></pre>
    </div>
    <h3>Artifacts</h3>
    <div id="wsArtifacts"><div class="ws-empty">Loading…</div></div>`;

  host.querySelector('#wsUpload').addEventListener('click', async () => {
    const input = host.querySelector('#wsFile');
    if (!input.files.length) {
      toast('Choose a file first.', 'error');
      return;
    }
    const state = host.querySelector('#wsFileState');
    for (const file of input.files) {
      state.textContent = `Uploading ${file.name}…`;
      await guard(() => api.uploadFile(file));
    }
    state.textContent = 'Uploaded.';
    input.value = '';
    loadFiles(host);
  });

  host.querySelector('#wsAnalyse').addEventListener('click', async () => {
    const id = host.querySelector('#wsChartFile').value;
    if (!id) {
      toast('Upload a table first.', 'error');
      return;
    }
    const result = await guard(() => api.post('/api/artifacts/analyse',
      { attachment_id: id }));
    if (!result) return;
    const pre = host.querySelector('#wsProfile');
    pre.hidden = false;
    pre.textContent = result.summary;
  });

  host.querySelector('#wsChart').addEventListener('click', async () => {
    const id = host.querySelector('#wsChartFile').value;
    if (!id) {
      toast('Upload a table first.', 'error');
      return;
    }
    const res = await guard(() => api.post('/api/artifacts/chart', {
      attachment_id: id,
      chart_type: host.querySelector('#wsChartType').value,
      x: host.querySelector('#wsChartX').value.trim(),
      y: host.querySelector('#wsChartY').value.trim(),
      title: host.querySelector('#wsChartTitle').value.trim(),
    }));
    if (res) {
      toast('Chart created.', 'success');
      loadFiles(host);
    }
  });

  loadFiles(host);
}

function openLightbox(src, name) {
  const box = h(`<div class="ws-lightbox" role="dialog" aria-modal="true"
    aria-label="${esc(name || 'Artifact')}"><img alt="${esc(name || '')}" src="${esc(src)}" /></div>`);
  const close = () => {
    box.remove();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (event) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      close();
    }
  };
  box.addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  document.body.appendChild(box);
}

async function loadFiles(host) {
  const [attachments, artifacts] = await guard(() => Promise.all([
    api.get('/api/attachments'), api.get('/api/artifacts'),
  ])) || [{ attachments: [] }, { artifacts: [] }];

  const select = host.querySelector('#wsChartFile');
  const list = host.querySelector('#wsAttachments');
  const previous = select.value;
  select.innerHTML = '';
  list.innerHTML = '';
  if (!attachments.attachments.length) {
    list.appendChild(h('<div class="ws-empty">No uploads yet.</div>'));
  }
  for (const file of attachments.attachments) {
    select.appendChild(h(`<option value="${file.id}">${esc(file.filename)}</option>`));
    const item = h(`<div class="ws-row" style="padding:.3rem 0">
      <div style="flex:1;min-width:0"><div class="ws-card-title">${esc(file.filename)}</div>
        <div class="ws-card-sub">${bytes(file.size)} · ${esc(file.mime || 'unknown type')}</div></div>
      <button class="ws-btn is-danger" type="button">Delete</button></div>`);
    item.querySelector('button').addEventListener('click', async () => {
      await guard(() => api.del(`/api/attachments/${file.id}`));
      loadFiles(host);
    });
    list.appendChild(item);
  }
  if (previous) select.value = previous;

  const grid = host.querySelector('#wsArtifacts');
  grid.innerHTML = '';
  if (!artifacts.artifacts.length) {
    grid.appendChild(h('<div class="ws-empty">Nothing produced yet.</div>'));
    return;
  }
  const wrap = h('<div class="ws-artifacts"></div>');
  for (const artifact of artifacts.artifacts) {
    const tile = h(`<div class="ws-artifact">
      ${artifact.kind === 'image'
        ? `<img alt="" loading="lazy" src="${esc(artifact.download_url)}" />`
        : '<div style="padding:1.2rem .6rem;text-align:center;font-size:11px">'
          + `${esc(artifact.kind)}</div>`}
      <div class="ws-artifact-meta"><div>${esc(artifact.name)}</div>
        <div class="ws-card-sub">${bytes(artifact.size)} · ${when(artifact.created_at)}</div>
        <div class="ws-row" style="margin-top:.35rem">
          <button class="ws-btn" data-act="get" type="button">Download</button>
          <button class="ws-btn is-danger" data-act="del" type="button">Delete</button>
        </div></div></div>`);
    tile.querySelector('[data-act="get"]').addEventListener('click', () => {
      api.download(artifact.download_url).catch(fail);
    });
    const preview = tile.querySelector('img');
    if (preview) {
      preview.addEventListener('click', () => openLightbox(preview.src, artifact.name));
    }
    tile.querySelector('[data-act="del"]').addEventListener('click', async () => {
      await guard(() => api.del(`/api/artifacts/${artifact.id}`));
      loadFiles(host);
    });
    wrap.appendChild(tile);
  }
  grid.appendChild(wrap);
}

/* ------------------------------------------------------------------ tasks */
async function renderTasks() {
  const host = panel('Background tasks',
    'Run a task once or on a schedule. It keeps running when you close this '
    + 'panel, and reports when it finishes.');
  host.innerHTML = `
    <form class="ws-card" id="wsTaskForm">
      <label class="ws-field"><span>Task</span>
        <textarea name="goal" placeholder="Summarise new GitHub issues in my repo each morning."></textarea></label>
      <div class="ws-row">
        <label class="ws-field"><span>Schedule</span>
          <select name="schedule"><option value="once">once</option>
            <option value="interval">recurring</option>
            <option value="cron">calendar (cron)</option></select></label>
        <label class="ws-field" id="wsIntervalField"><span>Every (seconds)</span>
          <input name="interval" type="number" min="5" value="3600"></label>
        <label class="ws-field" id="wsCronField" hidden><span>Cron expression</span>
          <input name="cron" placeholder="0 9 * * mon-fri" autocomplete="off"
            spellcheck="false"></label>
        <label class="ws-field"><span>Kind</span>
          <select name="kind"><option value="agent">agent (tools)</option>
            <option value="prompt">single answer</option></select></label>
      </div>
      <div class="ws-card-sub" id="wsCronPreview" role="status" aria-live="polite"></div>
      <div class="ws-row"><button class="ws-btn is-primary" type="submit">Schedule</button>
        <label class="ws-card-sub"><input type="checkbox" name="notify" checked> notify me</label></div>
    </form>
    <h3>Scheduled</h3>
    <div id="wsJobs"><div class="ws-empty">Loading…</div></div>
    <h3>Notifications</h3>
    <div class="ws-row"><button class="ws-btn" id="wsReadAll" type="button">Mark all read</button></div>
    <div id="wsNotes"><div class="ws-empty">Loading…</div></div>`;

  const taskForm = host.querySelector('#wsTaskForm');
  const cronPreview = host.querySelector('#wsCronPreview');
  const intervalField = host.querySelector('#wsIntervalField');
  const cronField = host.querySelector('#wsCronField');
  // Whether the expression currently typed parses. Set by the debounced check
  // below, so the form does not have to guess and the server stays the judge.
  let cronState = { expression: '', valid: false };

  const syncScheduleFields = () => {
    const mode = taskForm.schedule.value;
    intervalField.hidden = mode !== 'interval';
    cronField.hidden = mode !== 'cron';
    if (mode !== 'cron') {
      cronPreview.textContent = '';
      cronPreview.style.color = '';
    } else if (taskForm.cron.value.trim()) {
      checkCron(taskForm.cron.value.trim());
    }
  };

  let cronTimer = null;
  async function checkCron(expression) {
    const res = await guard(() => api.get(
      `/api/cron/preview?expression=${encodeURIComponent(expression)}`));
    if (!res) return;
    if (taskForm.cron.value.trim() !== expression) return;  // a later keystroke won
    cronState = { expression, valid: !!res.valid };
    if (res.valid) {
      cronPreview.textContent = `Next: ${res.next_local.join('  ·  ')}`;
      cronPreview.style.color = '';
    } else {
      cronPreview.textContent = res.error;
      cronPreview.style.color = 'var(--danger)';
    }
  }

  taskForm.schedule.addEventListener('change', syncScheduleFields);
  taskForm.cron.addEventListener('input', () => {
    clearTimeout(cronTimer);
    const expression = taskForm.cron.value.trim();
    if (!expression) {
      cronPreview.textContent = '';
      cronState = { expression: '', valid: false };
      return;
    }
    cronTimer = setTimeout(() => checkCron(expression), 250);
  });
  syncScheduleFields();

  taskForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const body = {
      goal: form.goal.value.trim(),
      kind: form.kind.value,
      schedule: form.schedule.value,
      notify: form.notify.checked,
    };
    if (body.schedule === 'interval') body.interval_sec = Number(form.interval.value);
    if (body.schedule === 'cron') {
      const expression = form.cron.value.trim();
      if (!expression) {
        toast('Enter a cron expression, for example 0 9 * * mon-fri.', 'error');
        return;
      }
      if (!cronState.valid || cronState.expression !== expression) {
        toast('That cron expression is not valid yet.', 'error');
        return;
      }
      body.cron = expression;
    }
    if (!body.goal) {
      toast('Describe the task first.', 'error');
      return;
    }
    const res = await guard(() => api.post('/api/jobs', body));
    if (res) {
      toast('Task scheduled.', 'success');
      form.reset();
      cronState = { expression: '', valid: false };
      syncScheduleFields();
      loadJobs(host);
    }
  });

  host.querySelector('#wsReadAll').addEventListener('click', async () => {
    await guard(() => api.post('/api/notifications/read-all', {}));
    loadJobs(host);
  });

  loadJobs(host);
}

async function loadJobs(host) {
  const [jobs, notes] = await guard(() => Promise.all([
    api.get('/api/jobs'), api.get('/api/notifications'),
  ])) || [{ jobs: [] }, { notifications: [] }];

  const list = host.querySelector('#wsJobs');
  list.innerHTML = '';
  if (!jobs.jobs.length) {
    list.appendChild(h('<div class="ws-empty">Nothing scheduled.</div>'));
  }
  for (const job of jobs.jobs) {
    const kind = job.status === 'done' ? 'ok'
      : job.status === 'failed' ? 'bad'
        : job.status === 'paused' ? 'warn' : '';
    const card = h(`<div class="ws-card">
      <div class="ws-card-head">
        <div><div class="ws-card-title">${esc(job.name)}</div>
          <div class="ws-card-sub">${esc(job.schedule)}
            ${job.schedule === 'interval' ? `· every ${job.interval_sec}s` : ''}
            ${job.schedule === 'cron' && job.cron ? `· ${esc(job.cron)}` : ''}
            · ${job.run_count} runs · next ${when(job.next_run_at)}</div></div>
        <div class="ws-row">${badge(job.status, kind)}</div>
      </div>
      ${job.last_result || job.error
        ? `<div class="ws-card-sub">${esc((job.error || job.last_result).slice(0, 220))}</div>` : ''}
      <div class="ws-row" style="margin-top:.4rem">
        <button class="ws-btn" data-act="run" type="button">Run now</button>
        <button class="ws-btn" data-act="pause" type="button">${job.status === 'paused' ? 'Resume' : 'Pause'}</button>
        <button class="ws-btn is-danger" data-act="cancel" type="button">Cancel</button>
        <button class="ws-btn is-danger" data-act="delete" type="button">Delete</button>
      </div></div>`);
    card.querySelector('[data-act="run"]').addEventListener('click', async () => {
      await guard(() => api.post(`/api/jobs/${job.id}/run`, {}));
      toast('Queued to run now.', 'success');
      loadJobs(host);
    });
    card.querySelector('[data-act="pause"]').addEventListener('click', async () => {
      const path = job.status === 'paused' ? 'resume' : 'pause';
      await guard(() => api.post(`/api/jobs/${job.id}/${path}`, {}));
      loadJobs(host);
    });
    card.querySelector('[data-act="cancel"]').addEventListener('click', async () => {
      await guard(() => api.post(`/api/jobs/${job.id}/cancel`, {}));
      loadJobs(host);
    });
    card.querySelector('[data-act="delete"]').addEventListener('click', () => {
      confirmDialog({
        title: 'Delete this task?',
        body: 'The schedule and its run history are removed.',
        confirmLabel: 'Delete', danger: true,
        onConfirm: async () => {
          await guard(() => api.del(`/api/jobs/${job.id}`));
          loadJobs(host);
        },
      });
    });
    list.appendChild(card);
  }

  const noteHost = host.querySelector('#wsNotes');
  noteHost.innerHTML = '';
  if (!notes.notifications.length) {
    noteHost.appendChild(h('<div class="ws-empty">No notifications.</div>'));
    return;
  }
  const ul = h('<ul class="ws-list"></ul>');
  for (const note of notes.notifications.slice(0, 15)) {
    const item = h(`<li><div><div class="ws-card-title">${esc(note.title)}</div>
      <div class="ws-card-sub">${esc(note.body.slice(0, 200))}</div>
      <div class="ws-card-sub">${when(note.created_at)}</div></div>
      ${note.read ? '' : '<button class="ws-btn" type="button">Read</button>'}</li>`);
    const button = item.querySelector('button');
    if (button) {
      button.addEventListener('click', async () => {
        await guard(() => api.post(`/api/notifications/${note.id}/read`, {}));
        loadJobs(host);
      });
    }
    ul.appendChild(item);
  }
  noteHost.appendChild(ul);
}

/* -------------------------------------------------------------- assistants */
async function renderAssistants() {
  const host = panel('Assistants and templates',
    'A saved assistant adds its instructions to every message you send while it '
    + 'is selected. Templates insert text into the composer.');
  host.innerHTML = `
    <form class="ws-card" id="wsAssistantForm">
      <div class="ws-row">
        <label class="ws-field"><span>Name</span><input name="name" placeholder="Editor"></label>
        <label class="ws-field" style="flex:0 0 5rem"><span>Icon</span>
          <input name="icon" maxlength="2" placeholder="✎"></label>
      </div>
      <label class="ws-field"><span>Instructions</span>
        <textarea name="instructions" placeholder="Answer in one short paragraph, cite the source."></textarea></label>
      <div class="ws-row">
        <label class="ws-field"><span>Model (optional)</span>
          <input name="model" placeholder="leave empty to use the current model"></label>
        <label class="ws-field"><span>Greeting (optional)</span>
          <input name="greeting" placeholder="What should I edit today?"></label>
      </div>
      <button class="ws-btn is-primary" type="submit">Save assistant</button>
    </form>
    <h3>Saved</h3>
    <div id="wsAssistantList"><div class="ws-empty">Loading…</div></div>
    <h3>Templates</h3>
    <form class="ws-card" id="wsTemplateForm">
      <div class="ws-row">
        <label class="ws-field"><span>Name</span><input name="name" placeholder="Summarise"></label>
        <label class="ws-field"><span>Category</span><input name="category" placeholder="writing"></label>
      </div>
      <label class="ws-field"><span>Body</span>
        <textarea name="body" placeholder="Summarise the following in five bullets:"></textarea></label>
      <button class="ws-btn" type="submit">Save template</button>
    </form>
    <div id="wsTemplateList"></div>`;

  host.querySelector('#wsAssistantForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    if (!form.name.value.trim()) {
      toast('Give the assistant a name.', 'error');
      return;
    }
    const res = await guard(() => api.post('/api/assistants', {
      name: form.name.value.trim(),
      instructions: form.instructions.value.trim(),
      model: form.model.value.trim(),
      greeting: form.greeting.value.trim(),
      icon: form.icon.value.trim(),
    }));
    if (res) {
      toast('Assistant saved.', 'success');
      form.reset();
      loadAssistants(host);
    }
  });

  host.querySelector('#wsTemplateForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    if (!form.name.value.trim() || !form.body.value.trim()) {
      toast('A template needs a name and a body.', 'error');
      return;
    }
    const res = await guard(() => api.post('/api/templates', {
      name: form.name.value.trim(),
      body: form.body.value.trim(),
      category: form.category.value.trim(),
    }));
    if (res) {
      form.reset();
      loadAssistants(host);
    }
  });

  loadAssistants(host);
}

async function loadAssistants(host) {
  const data = await guard(() => api.get('/api/assistants'));
  if (!data) return;

  const list = host.querySelector('#wsAssistantList');
  list.innerHTML = '';
  if (!data.assistants.length) {
    list.appendChild(h('<div class="ws-empty">No assistants saved.</div>'));
  }
  for (const assistant of data.assistants) {
    const active = window.__toleshAssistantId === assistant.id;
    const card = h(`<div class="ws-card">
      <div class="ws-card-head">
        <div><div class="ws-card-title">${esc(assistant.icon || '')} ${esc(assistant.name)}</div>
          <div class="ws-card-sub">${esc((assistant.instructions || 'No instructions').slice(0, 140))}</div>
          <div class="ws-card-sub">model: ${esc(assistant.model || 'current default')}</div></div>
        <div class="ws-row">${active ? badge('selected', 'ok') : ''}
          <button class="ws-btn" data-act="use" type="button">${active ? 'Clear' : 'Use'}</button>
          <button class="ws-btn is-danger" data-act="delete" type="button">Delete</button></div>
      </div></div>`);
    card.querySelector('[data-act="use"]').addEventListener('click', () => {
      window.__toleshAssistantId = active ? null : assistant.id;
      if (!active && assistant.greeting) {
        const input = document.getElementById('input');
        if (input && !input.value.trim()) {
          input.value = assistant.greeting;
          input.dispatchEvent(new Event('input', { bubbles: true }));
        }
      }
      toast(active ? 'Assistant cleared.' : `${assistant.name} selected for new messages.`,
        'success');
      loadAssistants(host);
    });
    card.querySelector('[data-act="delete"]').addEventListener('click', async () => {
      await guard(() => api.del(`/api/assistants/${assistant.id}`));
      if (window.__toleshAssistantId === assistant.id) window.__toleshAssistantId = null;
      loadAssistants(host);
    });
    list.appendChild(card);
  }

  const templates = host.querySelector('#wsTemplateList');
  templates.innerHTML = '';
  if (!data.templates.length) {
    templates.appendChild(h('<div class="ws-empty">No templates yet.</div>'));
    return;
  }
  const ul = h('<ul class="ws-list"></ul>');
  for (const template of data.templates) {
    const item = h(`<li><div><div class="ws-card-title">${esc(template.name)}</div>
      <div class="ws-card-sub">${esc(template.body.slice(0, 120))}</div></div>
      <div class="ws-row"><button class="ws-btn" data-act="use" type="button">Insert</button>
      <button class="ws-btn is-danger" data-act="delete" type="button">Delete</button></div></li>`);
    item.querySelector('[data-act="use"]').addEventListener('click', () => {
      const input = document.getElementById('input');
      if (!input) return;
      input.value = input.value ? `${input.value}\n${template.body}` : template.body;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.focus();
      closeWorkspace();
    });
    item.querySelector('[data-act="delete"]').addEventListener('click', async () => {
      await guard(() => api.del(`/api/templates/${template.id}`));
      loadAssistants(host);
    });
    ul.appendChild(item);
  }
  templates.appendChild(ul);
}

/* ------------------------------------------------------------------ memory */
/* The memory panel is the point of automatic memory: it has to be possible to
   see, search, correct and delete everything Tolesh taught itself, and to
   switch the whole thing off. Every row shows where it came from, how sure
   Tolesh is, and which project it belongs to. */

const MEMORY_CATEGORIES = ['conversation', 'preference', 'personal', 'project', 'task'];

function confidenceLabel(value) {
  if (value >= 0.9) return 'certain';
  if (value >= 0.7) return 'likely';
  if (value >= 0.5) return 'guess';
  return 'weak';
}

async function renderMemory() {
  const host = panel('Memory',
    'What Tolesh remembers about you, why, and how to change or delete it.');
  host.innerHTML = '<div class="ws-empty">Loading…</div>';
  const state = { q: '', category: '', showSuperseded: false };

  const [settings, status] = await guard(() => Promise.all([
    api.get('/api/memory/settings'), api.get('/api/memory/status'),
  ])) || [null, null];
  if (!settings) {
    host.innerHTML = '<div class="ws-empty">Memory settings are unavailable.</div>';
    return;
  }

  host.innerHTML = '';

  /* --- switches ------------------------------------------------------- */
  const values = settings.values || {};
  const descriptions = settings.descriptions || {};
  const switches = h('<div class="ws-card"><div class="ws-card-head">' +
    '<div><div class="ws-card-title">Learning and recall</div>' +
    '<div class="ws-card-sub">These three decide whether anything is stored, ' +
    'learned in the background, or put into an answer.</div></div></div>' +
    '<div data-toggles></div><div data-extra></div></div>');
  const toggleHost = switches.querySelector('[data-toggles]');
  for (const key of ['memory_enabled', 'memory_auto_extract', 'memory_retrieval']) {
    const row = h(`<label class="ws-toggle">
      <input type="checkbox" data-key="${key}" ${values[key] ? 'checked' : ''} />
      <span class="ws-toggle-text"><strong>${esc(key.replaceAll('_', ' '))}</strong>
      <span>${esc(descriptions[key] || '')}</span></span></label>`);
    row.querySelector('input').addEventListener('change', async (event) => {
      const body = {}; body[key] = event.target.checked;
      const res = await guard(() => api.patch('/api/memory/settings', body));
      if (res) { toast('Saved.', 'success'); renderMemory(); }
      else event.target.checked = !event.target.checked;
    });
    toggleHost.appendChild(row);
  }

  const extra = switches.querySelector('[data-extra]');
  const categories = new Set(values.memory_categories || []);
  const catRow = h(`<div class="ws-field"><span>Kinds that may be learned automatically</span>
    <div class="ws-row" data-cats></div></div>`);
  for (const category of MEMORY_CATEGORIES) {
    const allowed = category !== 'conversation';
    const chip = h(`<label class="ws-toggle" style="padding:.25rem .5rem">
      <input type="checkbox" value="${category}" ${categories.has(category) ? 'checked' : ''}
        ${allowed ? '' : 'disabled'} />
      <span class="ws-toggle-text"><span>${esc(category)}${allowed ? '' : ' (manual only)'}</span></span>
    </label>`);
    chip.querySelector('input').addEventListener('change', async () => {
      const picked = [...catRow.querySelectorAll('input:checked')].map((i) => i.value);
      const res = await guard(() => api.patch('/api/memory/settings',
        { memory_categories: picked }));
      if (res) toast('Saved.', 'success');
    });
    catRow.querySelector('[data-cats]').appendChild(chip);
  }
  extra.appendChild(catRow);

  const mode = h(`<div class="ws-row" style="margin-top:.75rem">
    <label class="ws-field"><span>How to read conversations</span>
      <select id="wsMemMode">
        <option value="rules">Rules (deterministic, free)</option>
        <option value="model">A model (slower, better with oblique phrasing)</option>
      </select></label>
    <label class="ws-field"><span>Memories per answer</span>
      <input type="number" id="wsMemLimit" min="1" max="12"
             value="${esc(values.memory_retrieval_limit || 6)}"></label></div>`);
  mode.querySelector('#wsMemMode').value = values.memory_extraction_mode || 'rules';
  mode.querySelector('#wsMemMode').addEventListener('change', async (event) => {
    const res = await guard(() => api.patch('/api/memory/settings',
      { memory_extraction_mode: event.target.value }));
    if (res) toast(event.target.value === 'model'
      ? 'Extraction will use a model when one is configured.'
      : 'Extraction uses the rule engine.', 'success');
  });
  mode.querySelector('#wsMemLimit').addEventListener('change', async (event) => {
    const value = Number(event.target.value);
    const res = await guard(() => api.patch('/api/memory/settings',
      { memory_retrieval_limit: value }));
    if (res) toast(`Up to ${value} memories per answer.`, 'success');
    else event.target.value = values.memory_retrieval_limit;
  });
  extra.appendChild(mode);
  host.appendChild(switches);

  /* --- stats ---------------------------------------------------------- */
  const stats = (status && status.stats) || {};
  const counters = (status && status.status && status.status.counters) || {};
  const summary = h(`<div class="ws-card"><div class="ws-card-head">
    <div><div class="ws-card-title">${stats.total || 0} active memories</div>
    <div class="ws-card-sub">${stats.automatic || 0} learned automatically ·
      ${stats.manual || 0} added by you · ${stats.superseded || 0} replaced ·
      ${stats.suppressions || 0} things you asked to forget</div></div>
    <button class="ws-btn" type="button" data-act="learn">Learn from this chat</button></div>
    <div class="ws-card-sub" data-runline></div></div>`);
  const runline = summary.querySelector('[data-runline]');
  const last = (status && status.status && status.status.last_run) || {};
  runline.textContent = last.ts
    ? `Last background pass ${when(last.ts)}: ${last.created || 0} new, `
      + `${last.corrected || 0} corrected, ${last.forgotten || 0} forgotten.`
    : 'No background pass has run yet in this session.';
  summary.querySelector('[data-act="learn"]').addEventListener('click', async () => {
    const cid = conversationId();
    if (!cid) { toast('Open a conversation first.', 'info'); return; }
    const button = summary.querySelector('[data-act="learn"]');
    button.disabled = true;
    button.textContent = 'Reading…';
    const res = await guard(() => api.post('/api/memory/extract', { conversation_id: cid }));
    button.disabled = false;
    button.textContent = 'Learn from this chat';
    if (!res) return;
    const total = res.created.length + res.merged.length + res.corrected.length;
    toast(total
      ? `${total} memor${total === 1 ? 'y' : 'ies'} saved.`
      : 'Nothing new worth keeping in this conversation.', 'success');
    renderMemory();
  });
  host.appendChild(summary);

  /* --- filters -------------------------------------------------------- */
  const filters = h(`<div class="ws-row">
    <label class="ws-field" style="flex:1"><span>Search</span>
      <input type="search" id="wsMemSearch" placeholder="Search what Tolesh remembers…"
             autocomplete="off"></label>
    <label class="ws-field"><span>Kind</span>
      <select id="wsMemFilter"><option value="">All</option>
      ${MEMORY_CATEGORIES.map((c) => `<option value="${c}">${c}</option>`).join('')}
      </select></label>
    <label class="ws-toggle" style="align-self:end"><input type="checkbox" id="wsMemHistory">
      <span class="ws-toggle-text"><span>Show replaced</span></span></label>
  </div>`);
  host.appendChild(filters);

  const listHost = h('<div data-list></div>');
  host.appendChild(listHost);

  const footer = h(`<div class="ws-row" style="margin-top:1rem">
    <button class="ws-btn is-danger" type="button" data-act="wipe">Forget everything</button>
    <span class="ws-card-sub">Deletes every memory and every "do not remember" note.</span>
  </div>`);
  footer.querySelector('[data-act="wipe"]').addEventListener('click', () => {
    confirmDialog({
      title: 'Forget everything?',
      body: 'This deletes all saved memories. It cannot be undone.',
      confirmLabel: 'Forget everything',
      danger: true,
      onConfirm: async () => {
        const res = await guard(() => api.post('/api/memory/forget',
          { confirm: 'FORGET EVERYTHING' }));
        if (res) { toast(`${res.deleted} memories deleted.`, 'success'); renderMemory(); }
      },
    });
  });
  host.appendChild(footer);

  const suppressions = h('<div data-supp></div>');
  host.appendChild(suppressions);

  async function loadList() {
    listHost.innerHTML = '<div class="ws-empty">Loading…</div>';
    const params = new URLSearchParams();
    if (state.q) params.set('q', state.q);
    if (state.category) params.set('category', state.category);
    if (state.showSuperseded) params.set('include_superseded', 'true');
    const data = await guard(() => api.get(`/api/memory?${params.toString()}`));
    if (!data) { listHost.innerHTML = ''; return; }
    listHost.innerHTML = '';
    if (!data.items.length) {
      listHost.appendChild(h(`<div class="ws-empty">${state.q
        ? 'Nothing matches that search.'
        : 'Nothing stored yet. Talk to Tolesh and it will learn what matters.'}</div>`));
      return;
    }
    for (const item of data.items) {
      listHost.appendChild(memoryRow(item, loadList));
    }
  }

  async function loadSuppressions() {
    const data = await guard(() => api.get('/api/memory/suppressions'));
    suppressions.innerHTML = '';
    if (!data || !data.suppressions.length) return;
    const card = h(`<div class="ws-card"><div class="ws-card-head">
      <div><div class="ws-card-title">Asked to forget</div>
      <div class="ws-card-sub">Tolesh will not learn these again. Removing a row
      lets a future conversation store the topic again.</div></div></div>
      <ul class="ws-list" data-rows></ul></div>`);
    for (const row of data.suppressions) {
      const li = h(`<li><div><div class="ws-card-title">${esc(row.phrase)}</div>
        <div class="ws-card-sub">Since ${when(row.created_at)}</div></div>
        <button class="ws-btn" type="button" data-act="allow">Allow again</button></li>`);
      li.querySelector('[data-act="allow"]').addEventListener('click', async () => {
        await guard(() => api.del(`/api/memory/suppressions/${row.id}`));
        toast('That topic may be remembered again.', 'success');
        loadSuppressions();
      });
      card.querySelector('[data-rows]').appendChild(li);
    }
    suppressions.appendChild(card);
  }

  let searchTimer = null;
  filters.querySelector('#wsMemSearch').addEventListener('input', (event) => {
    state.q = event.target.value.trim();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadList, 220);
  });
  filters.querySelector('#wsMemFilter').addEventListener('change', (event) => {
    state.category = event.target.value;
    loadList();
  });
  filters.querySelector('#wsMemHistory').addEventListener('change', (event) => {
    state.showSuperseded = event.target.checked;
    loadList();
  });

  loadList();
  loadSuppressions();
}

function memoryRow(item, reload) {
  const replaced = item.status === 'superseded';
  const card = h(`<div class="ws-card ${replaced ? 'is-muted' : ''}" data-id="${esc(item.id)}">
    <div class="ws-card-head">
      <div>
        <div class="ws-card-title" data-content>${esc(item.content)}</div>
        <div class="ws-card-sub">
          ${badge(item.category_label, item.category)}
          ${replaced ? badge('replaced', 'warn') : ''}
          ${item.pinned ? badge('pinned') : ''}
          ${item.disabled ? badge('switched off', 'warn') : ''}
          ${badge(item.origin === 'manual' ? 'you added this' : 'learned automatically')}
          confidence ${esc(confidenceLabel(item.confidence))}
          · used ${item.use_count}×
          · created ${when(item.created_at)}
          · updated ${when(item.updated_at)}
          ${item.project_id ? `· project ${esc(item.project_id.slice(0, 8))}` : '· every project'}
          ${item.superseded_by ? `· replaced by ${esc(item.superseded_by.slice(0, 8))}` : ''}
        </div>
      </div>
    </div>
    <div class="ws-row" data-actions></div>
  </div>`);
  const actions = card.querySelector('[data-actions]');

  const edit = h('<button class="ws-btn" type="button">Edit</button>');
  edit.addEventListener('click', () => {
    const title = card.querySelector('[data-content]');
    const box = h(`<div><textarea class="ws-field" rows="3"
      style="width:100%">${esc(item.content)}</textarea>
      <div class="ws-row"><button class="ws-btn" type="button" data-save>Save</button>
      <button class="ws-btn" type="button" data-cancel>Cancel</button></div></div>`);
    title.replaceWith(box);
    actions.hidden = true;
    box.querySelector('[data-cancel]').addEventListener('click', reload);
    box.querySelector('[data-save]').addEventListener('click', async () => {
      const content = box.querySelector('textarea').value.trim();
      if (!content) { toast('A memory cannot be empty.', 'error'); return; }
      const res = await guard(() => api.patch(`/api/memory/${item.id}`, { content }));
      if (res) { toast('Memory updated.', 'success'); reload(); }
    });
  });
  actions.appendChild(edit);

  const pin = h(`<button class="ws-btn" type="button">${item.pinned ? 'Unpin' : 'Pin'}</button>`);
  pin.addEventListener('click', async () => {
    const res = await guard(() => api.patch(`/api/memory/${item.id}`, { pinned: !item.pinned }));
    if (res) reload();
  });
  actions.appendChild(pin);

  const off = h(`<button class="ws-btn" type="button">
    ${item.disabled ? 'Switch on' : 'Switch off'}</button>`);
  off.addEventListener('click', async () => {
    const res = await guard(() => api.patch(`/api/memory/${item.id}`, { disabled: !item.disabled }));
    if (res) reload();
  });
  actions.appendChild(off);

  const del = h('<button class="ws-btn is-danger" type="button">Delete</button>');
  del.addEventListener('click', () => {
    confirmDialog({
      title: 'Delete this memory?',
      body: `"${item.content.slice(0, 120)}" will be removed, and will not be relearned from the same conversation.`,
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: async () => {
        await guard(() => api.del(`/api/memory/${item.id}`));
        toast('Deleted.', 'success');
        reload();
      },
    });
  });
  actions.appendChild(del);
  return card;
}

/* ---------------------------------------------------------------- settings */
async function renderSettings() {
  const host = panel('Settings and privacy',
    'What is stored, what is sent, and how to take it with you or remove it.');
  host.innerHTML = '<div class="ws-empty">Loading…</div>';
  const [config, media, caps] = await guard(() => Promise.all([
    api.get('/api/settings'), api.get('/api/media/capabilities'), api.get('/api/capabilities'),
  ])) || [{ values: {}, descriptions: {} }, {}, {}];

  host.innerHTML = '';
  const values = config.values || {};
  const descriptions = config.descriptions || {};

  const toggles = h('<div><h3>Behaviour</h3><div data-toggles></div></div>');
  for (const [key, value] of Object.entries(values)) {
    if (typeof value !== 'boolean') continue;
    const row = h(`<label class="ws-toggle">
      <input type="checkbox" data-key="${esc(key)}" ${value ? 'checked' : ''}
        ${key === 'telemetry' ? 'disabled' : ''} />
      <span class="ws-toggle-text"><strong>${esc(key.replaceAll('_', ' '))}</strong>
        <span>${esc(descriptions[key] || '')}</span></span></label>`);
    row.querySelector('input').addEventListener('change', async (event) => {
      const patch = {};
      patch[key] = event.target.checked;
      const res = await guard(() => api.patch('/api/settings', patch));
      if (res) toast('Saved.', 'success');
      else event.target.checked = !event.target.checked;
    });
    toggles.querySelector('[data-toggles]').appendChild(row);
  }
  host.appendChild(toggles);

  const sampling = h(`<div><h3>Response</h3>
    <label class="ws-field" style="max-width:12rem"><span>Temperature</span>
      <input type="number" id="wsTemp" min="0" max="2" step="0.1"
             value="${esc(localStorage.getItem('tolesh.temperature') || '')}"
             placeholder="provider default"></label>
    <p class="ws-card-sub">Sent with every message. 0 is as deterministic as the
      model gets; 1 is the usual default. Empty means "leave it to the provider".</p></div>`);
  sampling.querySelector('#wsTemp').addEventListener('change', (event) => {
    const raw = event.target.value.trim();
    const value = Number(raw);
    if (raw === '' || !Number.isFinite(value) || value < 0 || value > 2) {
      localStorage.removeItem('tolesh.temperature');
      event.target.value = '';
      toast('Using the provider default.', 'info');
      return;
    }
    localStorage.setItem('tolesh.temperature', String(value));
    toast(`Temperature set to ${value}.`, 'success');
  });
  host.appendChild(sampling);

  const numbers = h(`<div><h3>Limits</h3>
    <div class="ws-row">
      <label class="ws-field"><span>Agent steps</span>
        <input type="number" min="1" max="40" id="wsSteps" value="${values.agent_max_steps}"></label>
      <label class="ws-field"><span>Audit retention (days)</span>
        <input type="number" min="1" max="3650" id="wsRetention" value="${values.audit_retention_days}"></label>
    </div></div>`);
  const saveLimits = async () => {
    const res = await guard(() => api.patch('/api/settings', {
      agent_max_steps: Number(numbers.querySelector('#wsSteps').value),
      audit_retention_days: Number(numbers.querySelector('#wsRetention').value),
    }));
    if (res) toast('Limits saved.', 'success');
  };
  numbers.querySelector('#wsSteps').addEventListener('change', saveLimits);
  numbers.querySelector('#wsRetention').addEventListener('change', saveLimits);
  host.appendChild(numbers);

  const capsBox = h(`<div><h3>Capabilities on this machine</h3>
    <ul class="ws-list">
      <li><div><div class="ws-card-title">Speech to text</div>
        <div class="ws-card-sub">${esc(media.transcription?.reason || 'available')}</div></div>
        ${badge(media.transcription?.available ? 'available' : 'unavailable',
          media.transcription?.available ? 'ok' : 'bad')}</li>
      <li><div><div class="ws-card-title">Speech output</div>
        <div class="ws-card-sub">${esc(media.synthesis?.reason || 'available')}</div></div>
        ${badge(media.synthesis?.available ? 'available' : 'unavailable',
          media.synthesis?.available ? 'ok' : 'bad')}</li>
      <li><div><div class="ws-card-title">Image generation</div>
        <div class="ws-card-sub">${esc(media.image_generation?.reason || 'available')}</div></div>
        ${badge(media.image_generation?.available ? 'available' : 'unavailable',
          media.image_generation?.available ? 'ok' : 'bad')}</li>
      <li><div><div class="ws-card-title">MCP servers</div>
        <div class="ws-card-sub">${(caps.mcp_servers || []).length} configured</div></div></li>
    </ul></div>`);
  host.appendChild(capsBox);

  const data = h(`<div><h3>Your data</h3>
    <p class="ws-note">The export contains conversations, memory, projects, jobs,
      artifacts and uploaded files. It contains no API keys or connection tokens.</p>
    <div class="ws-row">
      <button class="ws-btn" id="wsExportAll" type="button">Download everything</button>
    </div>
    <div class="ws-card" style="margin-top:.8rem">
      <div class="ws-card-title">Delete all content</div>
      <p class="ws-card-sub">Removes conversations, memory, projects, files, artifacts,
        jobs and connections. Provider keys are kept unless you tick the box.</p>
      <div class="ws-row">
        <input id="wsConfirm" placeholder="Type DELETE ALL DATA" style="flex:1;min-width:12rem;
          border:1px solid var(--line,#e6e0d6);border-radius:8px;padding:.45rem .55rem">
        <label class="ws-card-sub"><input type="checkbox" id="wsKeys"> also delete keys</label>
        <button class="ws-btn is-danger" id="wsDeleteAll" type="button">Delete</button>
      </div>
    </div></div>`);
  data.querySelector('#wsExportAll').addEventListener('click', () => {
    api.download('/api/export').catch(fail);
  });
  data.querySelector('#wsDeleteAll').addEventListener('click', () => {
    const phrase = data.querySelector('#wsConfirm').value.trim();
    const includeKeys = data.querySelector('#wsKeys').checked;
    confirmDialog({
      title: 'Delete everything?',
      body: 'This cannot be undone. Type the phrase exactly, then confirm.',
      confirmLabel: 'Delete permanently', danger: true,
      onConfirm: async () => {
        const res = await guard(() => api.post('/api/data/delete',
          { confirm: phrase, include_keys: includeKeys }));
        if (res) {
          toast('Content deleted.', 'success');
          renderSettings();
        }
      },
    });
  });
  host.appendChild(data);

  const audit = h('<div><h3>Audit log</h3><div id="wsAudit"><div class="ws-empty">Loading…</div></div></div>');
  host.appendChild(audit);
  const events = await guard(() => api.get('/api/audit?limit=40'));
  const list = audit.querySelector('#wsAudit');
  list.innerHTML = '';
  if (!events || !events.events.length) {
    list.appendChild(h('<div class="ws-empty">Nothing recorded yet.</div>'));
    return;
  }
  const ul = h('<ul class="ws-list"></ul>');
  for (const event of events.events) {
    const kind = event.status === 'ok' || event.status === 'approved' || event.status === 'done'
      ? 'ok' : event.status === 'denied' || event.status === 'error' ? 'bad' : '';
    ul.appendChild(h(`<li><div><div class="ws-card-title">${esc(event.kind)} ·
      ${esc(String(event.action).slice(0, 80))}</div>
      <div class="ws-card-sub">${when(event.at)}</div></div>${badge(event.status, kind)}</li>`));
  }
  list.appendChild(ul);
}
