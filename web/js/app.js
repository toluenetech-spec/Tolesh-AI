/* Tolesh application bootstrap and wiring.
   This is a real client: it streams from the shared backend, renders memory,
   routes models, gates computer actions and speaks/listens via the browser. */

import * as api from './api.js';
import * as ui from './components.js';
import { initMotion, animateIn, setOrbListening, setCoreState, prefersReducedMotion } from './motion.js';
import { VoiceController } from './voice.js';

const $ = (id) => document.getElementById(id);

const state = {
  online: false,
  conversationId: null,
  titled: false,
  model: null,             // null = auto routing
  task: 'chat',
  providers: [],
  models: [],
  memory: [],
  memoryFilter: 'all',
  usage: {},
  tools: [],
  pending: [],
  streaming: false,
  abort: null,
  health: null,            // last /api/health payload, reused for re-renders
  route: [],               // the fallback chain, in the order it is tried
  conversations: [],       // history list, newest first (pinned on top)
  projects: [],
  projectFilter: 'all',    // 'all' | 'unfiled' | <project id>
  historyQuery: '',
  showArchived: false,
  attachments: [],         // uploaded files attached to the next message
  webSearch: false,        // opt-in per message, never silent
  conversationMeta: null,  // the open conversation, even when a filter hides it
};

const SUGGESTIONS = [
  'How far? Wetin dey?',
  'Ẹ káàárọ̀ — bawo ni?',
  'Explain what you remember about me',
  'What computer actions need my approval?',
];

/* ------------------------------------------------------------- utilities */
function setStatus(text) { $('streamStatus').textContent = text || ''; }
/** The core shows the truth about the engine: idle only when a request could
 *  actually be made, offline when the backend is unreachable. */
function restCoreState() {
  if (!state.online) setCoreState('offline');
  else if (voice.active) setCoreState('listening');
  else setCoreState('idle');
}
function scrollToBottom() {
  const s = $('stream');
  s.scrollTop = s.scrollHeight;
}
function setConnection(online) {
  const el = $('connection');
  el.classList.toggle('is-online', online);
  el.classList.toggle('is-offline', !online);
  el.querySelector('.connection-text').textContent = online ? 'connected' : 'offline';
}
function modelLabel(id) {
  return state.models.find((m) => m.id === id)?.label || id;
}
function openContext(open) {
  $('appShell').classList.toggle('context-collapsed', !open);
  $('contextToggle').setAttribute('aria-expanded', String(open));
}
function contextIsOpen() {
  return !$('appShell').classList.contains('context-collapsed');
}
/** On phones the composer is fixed to the bottom edge, so the conversation has
 *  to reserve its real height. A static guess was hiding the last suggestion
 *  behind the composer as soon as the textarea grew. */
function syncComposerHeight() {
  const wrap = document.querySelector('.composer-wrap');
  if (!wrap) return;
  const h = Math.ceil(wrap.getBoundingClientRect().height);
  document.documentElement.style.setProperty('--composer-h', `${h}px`);
}
/* Stateful UI is deep-linkable: the active view lives in ?view=. */
function syncViewUrl(view) {
  try {
    const u = new URL(location.href);
    if (view === 'chat') u.searchParams.delete('view');
    else u.searchParams.set('view', view);
    history.replaceState(null, '', u.pathname + u.search + u.hash);
  } catch (_) { /* file:// and locked-down shells may forbid history writes */ }
}

/* -------------------------------------------------------------- bootstrap */
async function boot() {
  // The app-shell element is the grid; give it the id used by openContext().
  const shell = document.querySelector('.app-shell');
  shell.id = 'appShell';
  // On narrow screens the context panel is a drawer and must start closed,
  // otherwise it covers the entire conversation on first load.
  if (window.innerWidth <= 980) shell.classList.add('context-collapsed');
  document.body.dataset.coreState = 'idle';
  initMotion();

  const greeting = $('greetingWord');
  const h = new Date().getHours();
  greeting.textContent = h < 12 ? 'morning' : h < 17 ? 'afternoon' : 'evening';

  ui.renderSuggestions($('suggestions'), SUGGESTIONS, (text) => {
    $('input').value = text;
    $('input').focus();
  });

  // New conversation button.
  const newBtn = document.createElement('button');
  newBtn.className = 'icon-btn';
  newBtn.title = 'New conversation';
  newBtn.setAttribute('aria-label', 'New conversation');
  newBtn.innerHTML = ui.icon('plus');
  newBtn.addEventListener('click', newConversation);
  document.querySelector('.topbar-right').prepend(newBtn);

  bindEvents();
  syncComposerHeight();
  if ('ResizeObserver' in window) {
    new ResizeObserver(syncComposerHeight).observe(document.querySelector('.composer-wrap'));
  }
  window.addEventListener('resize', syncComposerHeight);
  const savedView = new URL(location.href).searchParams.get('view');
  if (savedView && savedView !== 'chat' && window.innerWidth > 980) {
    const btn = document.querySelector(`.rail-item[data-view="${CSS.escape(savedView)}"]`);
    if (btn) btn.click();
  }
  await refreshAll();
}

function newConversation() {
  if (state.streaming && state.abort) state.abort.abort();
  state.conversationId = null;
  state.conversationMeta = null;
  state.titled = false;
  state.streaming = false;
  $('messages').innerHTML = '';
  $('emptyState').style.display = '';
  $('convTitle').textContent = 'New conversation';
  $('sendBtn').disabled = false;
  $('sendBtn').classList.remove('is-busy');
  setStatus('');
  $('input').value = '';
  $('input').focus();
  state.attachments = [];
  renderAttachments();
  renderConversationHeader();
  restCoreState();
}

/* --------------------------------------------------------------- refresh */
async function refreshAll() {
  try {
    const health = await api.health();
    state.online = true;
    setConnection(true);
    const [providers, models, memory, usage, tools, pending, route] = await Promise.all([
      api.get('/api/providers'),
      api.get('/api/models'),
      api.get('/api/memory'),
      api.get('/api/usage'),
      api.get('/api/tools'),
      api.get('/api/permissions/pending'),
      api.get('/api/fallback-order'),
    ]);
    state.providers = providers.providers || [];
    state.models = models.models || [];
    state.memory = memory.items || [];
    state.usage = usage || {};
    state.tools = tools.tools || [];
    state.pending = pending.pending || [];
    state.route = route.detail || [];

    renderAll(health);
    restCoreState();
    await loadHistory();
  } catch (err) {
    state.online = false;
    setConnection(false);
    setCoreState('offline');
    renderComposerNote(null);
    ui.toast(`Backend not connected: ${err.message}`, 'error');
  }
}

async function silentRefresh() {
  if (!state.online) return;
  try {
    const [memory, usage, pending] = await Promise.all([
      api.get('/api/memory'), api.get('/api/usage'), api.get('/api/permissions/pending'),
    ]);
    state.memory = memory.items || [];
    state.usage = usage || {};
    state.pending = pending.pending || [];
    renderMemory();
    ui.renderUsage($('usageGrid'), state.usage);
    renderPermissions();
  } catch (_) { /* a failed background refresh must not break the chat */ }
}

/** The line under the composer has to describe the engine as it is now.
 *  A fixed sentence claiming "nothing is sent to a model until you connect a
 *  provider" is false the moment a provider is configured, so the text is
 *  derived from /api/health instead of being baked into the markup. */
function renderComposerNote(health) {
  const el = $('composerFootnote');
  if (!el) return;
  if (!state.online) {
    el.textContent = 'Engine offline — reopen the Tolesh window from the tray, or restart the app.';
    return;
  }
  const real = (health?.providers_configured || []).filter((p) => p !== 'offline');
  if (!real.length) {
    el.textContent = 'No model connected — replies come from the local offline engine, '
      + 'not a language model.';
    return;
  }
  const chosen = String(health?.default_model || '');
  const target = chosen && !chosen.startsWith('auto')
    ? modelLabel(chosen)
    : 'the best available model';
  // "Configured" is the honest word: a provider can be listed here and still be
  // unreachable (Ollama has a default URL and no key), which is what the
  // per-provider connection test is for.
  //
  // The chain comes from /api/health so the line states the real fallback plan
  // (DeepSeek → GLM → MiniMax) instead of hiding it behind "a sibling model".
  const chain = (health?.fallback_order || [])
    .filter((id) => !String(id).startsWith('offline:'));
  if (chain.length > 1) {
    const shown = chain.slice(0, 3).map(modelLabel).join(' → ');
    const rest = chain.length > 3 ? `, then ${chain.length - 3} more` : '';
    el.textContent = `Configured: ${real.join(', ')} · tries ${shown}${rest}, in that order.`;
    return;
  }
  el.textContent = `Configured: ${real.join(', ')} · the router starts with ${target} `
    + 'and falls back to a sibling model if it fails.';
}

function renderAll(health) {
  ui.renderEngine($('engineGrid'), health);
  renderComposerNote(health);
  $('engineBadge').textContent = state.online ? 'online' : 'offline';
  ui.renderModelSelector($('modelSelector'), {
    providers: state.providers,
    models: state.models,
    current: state.model,
    onSelect: (id) => {
      state.model = id;
      const p = state.providers.find((x) => x.name === state.models.find((m) => m.id === id)?.provider);
      if (p && !p.configured) {
        ui.toast('This provider needs an API key. Tolesh will try it and fall back if it fails.', 'error');
      } else {
        ui.toast(id ? `Model set to ${modelLabel(id)}` : 'Auto routing enabled', 'ok');
      }
      // Persist the choice so it survives an application restart.
      api.patch('/api/config', { default_model: id || '' }).catch((err) => {
        ui.toast(`Could not save model preference: ${err.message}`, 'error');
      });
      renderAll(health);
    },
  });
  ui.renderProviders($('providerList'), state.providers, { onConfigure: openProviderDialog });
  $('providerCount').textContent = String(state.providers.filter((p) => p.configured).length);
  renderRoute();
  ui.renderUsage($('usageGrid'), state.usage);
  ui.renderTools($('toolList'), state.tools, { onInvoke });
  renderMemoryFilters();
  renderMemory();
  renderPermissions();
}

/* -------------------------------------------------------- fallback order */
/** The chain is the router's, not the panel's: read it back from the server
 *  after every change so what is shown is what will actually be tried. */
async function refreshRoute() {
  try {
    const r = await api.get('/api/fallback-order');
    state.route = r.detail || [];
  } catch (_) {
    state.route = [];
  }
  renderRoute();
}

function renderRoute() {
  ui.renderRoute($('routeList'), state.route, {
    onMove: moveRoute,
    onToggle: toggleModel,
  });
}

async function moveRoute(id, direction) {
  // Only switched-on models are part of the chain; sending a parked id would
  // write a dead entry into the saved order.
  const ids = state.route.filter((m) => m.enabled).map((m) => m.id);
  const i = ids.indexOf(id);
  const j = direction === 'up' ? i - 1 : i + 1;
  if (i < 0 || j < 0 || j >= ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  try {
    await api.put('/api/fallback-order', { models: ids });
    await refreshRoute();
    ui.toast('Fallback order saved.', 'ok');
  } catch (err) {
    ui.toast(err.message, 'error');
    await refreshRoute();
  }
}

async function toggleModel(id, enabled) {
  try {
    await api.patch(`/api/models/${encodeURIComponent(id)}`, { enabled });
    await refreshRoute();
    // The chain lost or gained a step, so the composer footnote is now stale.
    if (state.health) renderComposerNote(state.health);
    ui.toast(`${modelLabel(id)} ${enabled ? 'enabled' : 'switched off'}.`, 'ok');
  } catch (err) {
    ui.toast(err.message, 'error');
    await refreshRoute();
  }
}

async function resetRoute() {
  try {
    await api.put('/api/fallback-order', { models: [] });
    await refreshRoute();
    ui.toast('Fallback order reset to the built-in chain.', 'ok');
  } catch (err) {
    ui.toast(err.message, 'error');
  }
}

function renderMemoryFilters() {
  const cats = [['all', 'All'], ['preference', 'Preferences'], ['personal', 'Personal'],
                ['project', 'Projects'], ['task', 'Tasks']];
  $('memoryFilters').innerHTML = cats.map(([k, l]) =>
    `<button class="chip ${state.memoryFilter === k ? 'is-active' : ''}" data-cat="${k}">${l}</button>`
  ).join('');
  $('memoryFilters').querySelectorAll('.chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      state.memoryFilter = chip.dataset.cat;
      renderMemoryFilters();
      renderMemory();
      $('memoryFilters').querySelector(`[data-cat="${state.memoryFilter}"]`)?.focus();
    });
  });
}

function renderMemory() {
  const items = state.memoryFilter === 'all'
    ? state.memory
    : state.memory.filter((m) => m.category === state.memoryFilter);
  ui.renderMemory($('memoryList'), items, { onDelete: deleteMemory });
  $('h-memory').textContent = `Memory (${state.memory.length})`;
}

function renderPermissions() {
  ui.renderPermissions($('permissionList'), state.pending, {
    onApprove: decidePermission('approve'),
    onDeny: decidePermission('deny'),
  });
  $('permCount').textContent = String(state.pending.length);
}

/* -------------------------------------------------------- provider settings */
/** Save a provider patch, then refresh the panels that depend on it. */
async function saveProvider(name, patch) {
  const r = await api.patch(`/api/providers/${name}`, patch);
  const idx = state.providers.findIndex((p) => p.name === name);
  if (idx >= 0 && r.provider) state.providers[idx] = r.provider;
  if (patch.models) {
    // The catalogue changed, so the model picker is stale until reloaded.
    try {
      state.models = (await api.get('/api/models')).models;
      if (state.health) renderAll(state.health);
    } catch (_) { /* keep the previous list rather than blanking the picker */ }
  }
  ui.renderProviders($('providerList'), state.providers, { onConfigure: openProviderDialog });
  $('providerCount').textContent = String(state.providers.filter((p) => p.configured).length);
  return r;
}

function openProviderDialog(provider) {
  ui.providerDialog(provider, {
    onSave: async (patch) => {
      try {
        await saveProvider(provider.name, patch);
        ui.toast(`${provider.label} saved.`, 'ok');
      } catch (err) {
        ui.toast(err.message, 'error');
      }
    },
    onTest: async (patch) => {
      // Test what is actually stored: save first, then ask the backend to
      // make one real request. This cannot "pass" against unsaved form state.
      await saveProvider(provider.name, patch);
      return api.post(`/api/providers/${provider.name}/test`, {});
    },
    onDiscover: async (patch) => {
      // Same rule for discovery: the endpoint is queried with the saved key.
      await saveProvider(provider.name, patch);
      return api.get(`/api/providers/${provider.name}/models`);
    },
  });
}

/* ------------------------------------------------------------------- chat */
async function send(text) {
  const t = (text || '').trim();
  if (!t || state.streaming) return;
  if (!state.online) {
    ui.toast('The Tolesh engine is not connected. Reopen this window from the tray.', 'error');
    return;
  }
  state.streaming = true;
  $('sendBtn').disabled = true;
  $('sendBtn').classList.add('is-busy');
  $('emptyState').style.display = 'none';
  $('input').value = '';
  setCoreState('thinking');

  const userEl = ui.createMessage('user', t);
  $('messages').appendChild(userEl);
  animateIn(userEl);

  const botEl = ui.createMessage('assistant', '', { streaming: true });
  $('messages').appendChild(botEl);
  animateIn(botEl);
  scrollToBottom();

  let buffer = '';
  let usedModel = null;
  state.fellBack = false;
  state.webFailed = false;
  state.lastProviderError = null;
  const controller = new AbortController();
  state.abort = controller;

  const onEvent = (ev) => {
    switch (ev.type) {
      case 'meta': {
        state.conversationId = ev.data.conversation_id;
        if (!state.titled) {
          state.titled = true;
          const title = t.length > 42 ? t.slice(0, 42) + '…' : t;
          $('convTitle').textContent = title;
          api.patch(`/api/conversations/${encodeURIComponent(state.conversationId)}`, { title })
            .catch(() => {});
        }
        break;
      }
      case 'status': {
        const d = ev.data || {};
        if (d.kind === 'select') setStatus(`Routing to ${modelLabel(d.model)}…`);
        else if (d.kind === 'provider_error') {
          const msg = String(d.message || 'unknown error');
          // A queued model (Dahl answers 429 model_concurrency on the free tier)
          // is not a broken key: say so instead of showing a scary failure.
          const busy = d.status === 429 || /concurrency|capacity|rate.?limit/i.test(msg);
          state.lastProviderError = msg;
          setStatus(busy
            ? `${modelLabel(d.model)} is at capacity — trying the next model…`
            : `Provider failed: ${msg.slice(0, 90)}`);
        }
        else if (d.kind === 'retry') setStatus(`Retrying in ${d.delay_ms}ms…`);
        else if (d.kind === 'fallback') setStatus(`Falling back to ${modelLabel(d.to)}…`);
        else if (d.kind === 'web_fetch') setStatus(`Reading ${String(d.url).slice(0, 60)}…`);
        else if (d.kind === 'web_search') setStatus(`Searching the web for “${String(d.query).slice(0, 50)}”…`);
        else if (d.kind === 'web_ok') setStatus(`Read ${d.title || d.url} (${d.chars} chars).`);
        else if (d.kind === 'web_error') {
          // The message still gets answered from what is left; say what failed
          // instead of silently answering as if the web had been consulted.
          state.webFailed = true;
          setStatus(`Web unavailable: ${String(d.message || '').slice(0, 110)}`);
        }
        break;
      }
      case 'token':
        if (!buffer) setCoreState('responding');
        buffer += ev.data;
        ui.setMessageText(botEl, buffer, true);
        scrollToBottom();
        break;
      case 'reset':
        buffer = '';
        ui.setMessageText(botEl, '', true);
        break;
      case 'usage':
        if (ev.data) {
          state.usage.total_tokens = (state.usage.total_tokens || 0) + (ev.data.total_tokens || 0);
        }
        break;
      case 'done':
        usedModel = ev.data?.model || null;
        state.fellBack = (ev.data?.fallbacks_used || 0) > 0;
        setStatus('');
        break;
      case 'error': {
        setStatus('Every model in the chain failed.');
        const detail = state.lastProviderError
          ? `${ev.data?.message || 'The model chain failed.'} ${state.lastProviderError}`
          : (ev.data?.message || 'The model chain failed.');
        ui.toast(detail, 'error');
        break;
      }
      default:
        break;
    }
  };

  try {
    const ready = state.attachments
      .filter((a) => a.id && !a.pending && !a.error)
      .map((a) => a.id);
    // URLs typed in the message are only read when the user switched web on:
    // fetching a link is a network action, not a rendering detail.
    const urls = state.webSearch ? (t.match(/https?:\/\/[^\s<>"')]+/g) || []).slice(0, 3) : [];
    await api.chatStream({
      message: t,
      conversation_id: state.conversationId,
      model: state.model,
      task: state.task,
      attachment_ids: ready,
      web_search: state.webSearch,
      web_urls: urls,
      project_id: targetProjectId(),
    }, onEvent, controller.signal);
  } catch (err) {
    if (err.name !== 'AbortError') {
      ui.setMessageText(botEl, `I couldn't reach the Tolesh engine: ${err.message}`, false);
      botEl.classList.add('is-error');
    }
  } finally {
    const tag = usedModel
      ? `${modelLabel(usedModel)}${state.fellBack ? ' · fallback' : ''}`
      : '';
    ui.finishMessage(botEl, state.webFailed ? `${tag}${tag ? ' · ' : ''}web failed` : tag);
    state.streaming = false;
    state.abort = null;
    $('sendBtn').disabled = false;
    $('sendBtn').classList.remove('is-busy');
    setStatus('');
    scrollToBottom();
    restCoreState();
    // The files were used for this turn; the uploads stay on disk with the
    // conversation but the composer starts clean.
    state.attachments = [];
    renderAttachments();
    if (voice.active && buffer) {
      voice.speak(buffer, {
        onStart: () => setCoreState('speaking'),
        onEnd: restCoreState,
      });
    }
    await silentRefresh();
    await loadHistory();
  }
}

/* -------------------------------------------------- history and projects */
function currentConversation() {
  if (!state.conversationId) return null;
  const found = state.conversations.find((c) => c.id === state.conversationId);
  if (found) { state.conversationMeta = found; return found; }
  // A search or project filter can hide the open conversation from the list;
  // the header still has to describe the conversation that is actually open.
  return state.conversationMeta?.id === state.conversationId
    ? state.conversationMeta : null;
}

/** The project a new message belongs to: the conversation's own project when
 *  one is open, otherwise whatever the history filter is set to. */
function targetProjectId() {
  const conv = currentConversation();
  if (conv) return conv.project_id || null;
  return (state.projectFilter !== 'all' && state.projectFilter !== 'unfiled')
    ? state.projectFilter : null;
}

async function loadHistory() {
  if (!state.online) return;
  const params = new URLSearchParams();
  if (state.historyQuery.trim()) params.set('q', state.historyQuery.trim());
  if (state.showArchived) params.set('include_archived', 'true');
  if (state.projectFilter === 'unfiled') params.set('unfiled', 'true');
  else if (state.projectFilter !== 'all') params.set('project_id', state.projectFilter);
  try {
    const [list, projects] = await Promise.all([
      api.get(`/api/conversations${params.toString() ? `?${params}` : ''}`),
      api.get('/api/projects'),
    ]);
    state.conversations = list.conversations || [];
    state.projects = projects.projects || [];
    const open = state.conversations.find((c) => c.id === state.conversationId);
    if (open) state.conversationMeta = open;
  } catch (err) {
    // A history failure must not look like a chat failure; say what broke.
    setStatus(`Could not load history: ${err.message}`);
    return;
  }
  renderHistoryPanel();
}

function renderHistoryPanel() {
  ui.renderProjectChips($('projectChips'), {
    projects: state.projects,
    filter: state.projectFilter,
    onSelect: (id) => { state.projectFilter = id; loadHistory(); },
  });
  ui.renderHistory($('historyList'), {
    conversations: state.conversations,
    activeId: state.conversationId,
    query: state.historyQuery,
    archived: state.showArchived,
    onOpen: openConversation,
    onPin: (id, pinned) => flagConversation(id, { pinned }),
    onArchive: (id, archived) => flagConversation(id, { archived }),
    onExport: exportConversation,
    onDelete: confirmDeleteConversation,
  });
  renderConversationHeader();
}

function renderConversationHeader() {
  const conv = currentConversation();
  if (conv) $('convTitle').textContent = conv.title;
  const projectId = targetProjectId();
  const project = projectId ? state.projects.find((p) => p.id === projectId) : null;
  $('projectChipName').textContent = project ? project.name : 'No project';
  $('projectChip').classList.toggle('is-set', Boolean(project));
  ui.renderConversationActions($('conversationActions'), {
    hasConversation: Boolean(state.conversationId),
    pinned: Boolean(conv?.pinned),
    archived: Boolean(conv?.archived),
    onPin: () => state.conversationId
      && flagConversation(state.conversationId, { pinned: !conv?.pinned }),
    onArchive: () => state.conversationId
      && flagConversation(state.conversationId, { archived: !conv?.archived }),
    onExport: () => state.conversationId && exportConversation(state.conversationId),
    onDelete: () => state.conversationId && confirmDeleteConversation(state.conversationId),
  });
}

async function openConversation(id) {
  if (!id) return;
  if (state.streaming && state.abort) state.abort.abort();
  try {
    const [data, meta] = await Promise.all([
      api.get(`/api/conversations/${encodeURIComponent(id)}/messages`),
      api.get(`/api/conversations/${encodeURIComponent(id)}`),
    ]);
    state.conversationId = id;
    state.conversationMeta = meta.conversation;
    state.titled = true;
    state.attachments = [];
    renderAttachments();
    $('messages').innerHTML = '';
    $('emptyState').style.display = (data.messages || []).length ? 'none' : '';
    for (const m of data.messages || []) {
      const el = ui.createMessage(m.role === 'user' ? 'user' : 'assistant', m.content);
      $('messages').appendChild(el);
      if (m.role !== 'user') ui.finishMessage(el, m.model ? modelLabel(m.model) : '');
    }
    scrollToBottom();
    renderHistoryPanel();
    if (window.innerWidth <= 980) openContext(false);
    $('input').focus();
  } catch (err) {
    ui.toast(`Could not open that conversation: ${err.message}`, 'error');
  }
}

async function flagConversation(id, patch) {
  try {
    const res = await api.patch(`/api/conversations/${encodeURIComponent(id)}/flags`, patch);
    if (state.conversationId === id && res.conversation) state.conversationMeta = res.conversation;
    await loadHistory();
    const what = patch.pinned !== undefined ? (patch.pinned ? 'Pinned' : 'Unpinned')
      : patch.archived !== undefined ? (patch.archived ? 'Archived' : 'Restored')
        : 'Moved';
    ui.toast(`${what}.`, 'ok');
  } catch (err) { ui.toast(err.message, 'error'); }
}

async function exportConversation(id) {
  try {
    const name = await api.download(`/api/conversations/${encodeURIComponent(id)}/export`);
    ui.toast(`Exported ${name}`, 'ok');
  } catch (err) { ui.toast(`Export failed: ${err.message}`, 'error'); }
}

function confirmDeleteConversation(id) {
  const conv = state.conversations.find((c) => c.id === id);
  ui.confirmDialog({
    title: 'Delete conversation',
    body: `“${conv?.title || 'This conversation'}” and its messages will be removed. `
      + 'This cannot be undone.',
    confirmLabel: 'Delete',
    danger: true,
    onConfirm: async () => {
      try {
        await api.del(`/api/conversations/${encodeURIComponent(id)}`);
        if (state.conversationId === id) newConversation();
        await loadHistory();
        ui.toast('Conversation deleted.', 'ok');
      } catch (err) { ui.toast(err.message, 'error'); }
    },
  });
}

function newProject() {
  ui.projectDialog({
    onSave: async (payload) => {
      try {
        const created = await api.post('/api/projects', payload);
        // Deliberately not switching the filter: doing so would hide every
        // conversation that is not in the new project, including the open one.
        ui.toast(`Project “${created.project.name}” created.`, 'ok');
        await loadHistory();
      } catch (err) { ui.toast(err.message, 'error'); }
    },
  });
}

function chooseProject() {
  const conv = currentConversation();
  ui.pickProjectDialog({
    projects: state.projects,
    current: targetProjectId(),
    onPick: async (pid) => {
      if (conv) {
        await flagConversation(conv.id, pid ? { project_id: pid } : { clear_project: true });
      } else {
        state.projectFilter = pid || 'all';
        renderHistoryPanel();
        ui.toast(pid ? 'New messages will be filed in that project.' : 'New messages are unfiled.', 'ok');
      }
    },
  });
}

/* ------------------------------------------------------------ attachments */
function renderAttachments() {
  ui.renderAttachments($('attachmentChips'), state.attachments, { onRemove: removeAttachment });
  syncComposerHeight();
}

async function attachFiles(files) {
  for (const file of files) {
    const key = `pending-${file.name}-${Date.now()}`;
    state.attachments.push({ id: key, filename: file.name, pending: true });
    renderAttachments();
    try {
      const res = await api.uploadFile(file);
      const at = state.attachments.findIndex((a) => a.id === key);
      if (at >= 0) state.attachments[at] = res.attachment;
    } catch (err) {
      const at = state.attachments.findIndex((a) => a.id === key);
      if (at >= 0) state.attachments[at] = { id: key, filename: file.name, error: err.message };
    }
    renderAttachments();
  }
}

async function removeAttachment(key) {
  const item = state.attachments.find((a) => (a.id || a.filename) === key);
  state.attachments = state.attachments.filter((a) => (a.id || a.filename) !== key);
  renderAttachments();
  if (item && !item.pending && !item.error && item.id) {
    // Deleting the upload keeps the file list honest: an attachment the user
    // removed should not still sit on disk.
    api.del(`/api/attachments/${encodeURIComponent(item.id)}`).catch(() => {});
  }
}

function toggleWebSearch() {
  state.webSearch = !state.webSearch;
  const btn = $('webBtn');
  btn.classList.toggle('is-active', state.webSearch);
  btn.setAttribute('aria-pressed', String(state.webSearch));
  setStatus(state.webSearch
    ? 'Web search is on for the next message.'
    : '');
}

/* -------------------------------------------------------------- actions */
async function addMemory() {
  ui.memoryDialog({
    onSubmit: async (category, content) => {
      try {
        await api.post('/api/memory', { category, content });
        ui.toast('Saved to memory.', 'ok');
        await silentRefresh();
      } catch (err) { ui.toast(err.message, 'error'); }
    },
  });
}

async function deleteMemory(id) {
  try {
    await api.del(`/api/memory/${encodeURIComponent(id)}`);
    ui.toast('Memory deleted.', 'ok');
    await silentRefresh();
  } catch (err) { ui.toast(err.message, 'error'); }
}

function forgetEverything() {
  ui.forgetDialog({
    onConfirm: async (phrase) => {
      try {
        const r = await api.post('/api/memory/forget', { confirm: phrase });
        ui.toast(`Deleted ${r.deleted} memory items.`, 'ok');
        await silentRefresh();
      } catch (err) { ui.toast(err.message, 'error'); }
    },
  });
}

async function onInvoke(name) {
  const needsArgs = ['list_directory', 'read_text_file', 'write_text_file',
                     'open_application', 'delete_file', 'run_command'];
  let args = {};
  if (needsArgs.includes(name)) {
    args = await ui.toolDialog(name);
    if (args === null) return;
  }
  try {
    const r = await api.post('/api/tools/invoke', {
      tool: name, args, reason: 'Invoked from the Tolesh context panel',
    });
    if (r.status === 'pending-confirmation' || r.status === 'pending') {
      ui.toast('Approval required — check the Context panel.', 'error');
      openContext(true);
      await silentRefresh();
    } else if (r.status === 'ok') {
      ui.toast(`${name} → success`, 'ok');
    } else {
      ui.toast(`${name}: ${r.message || r.status}`, 'error');
    }
  } catch (err) { ui.toast(err.message, 'error'); }
}

function decidePermission(kind) {
  return async (id) => {
    const item = state.pending.find((p) => p.id === id);
    if (!item) return;
    // The token is returned by the invoke response; for items loaded from the
    // pending list we ask the backend through the approval endpoint which
    // requires it. In the normal flow the token is held here.
    const token = item.token || item._token;
    if (!token) {
      ui.toast('Approval token unavailable for this item. It may have expired — request the action again.', 'error');
      await silentRefresh();
      return;
    }
    try {
      const r = await api.post(`/api/permissions/${encodeURIComponent(id)}/${kind}`, {
        token, execute: kind === 'approve',
      });
      ui.toast(kind === 'approve' ? 'Action approved.' : 'Action denied.', kind === 'approve' ? 'ok' : 'info');
      if (r.execution) ui.toast(`${r.execution.tool}: ${r.execution.status}`, r.execution.status === 'ok' ? 'ok' : 'error');
      await silentRefresh();
    } catch (err) { ui.toast(err.message, 'error'); }
  };
}

/* --------------------------------------------------------------- bindings */
function bindEvents() {
  $('composer').addEventListener('submit', (e) => {
    e.preventDefault();
    send($('input').value);
  });
  $('input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send($('input').value);
    }
  });
  $('input').addEventListener('input', () => {
    const el = $('input');
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  });

  $('contextToggle').addEventListener('click', () => openContext(!contextIsOpen()));
  $('contextClose').addEventListener('click', () => openContext(false));
  $('addMemoryBtn').addEventListener('click', addMemory);
  $('forgetBtn').addEventListener('click', forgetEverything);
  $('resetOrderBtn').addEventListener('click', resetRoute);

  // ---- history, projects, attachments, web
  $('newProjectBtn').addEventListener('click', newProject);
  $('projectChip').addEventListener('click', chooseProject);
  $('archivedToggle').addEventListener('click', () => {
    state.showArchived = !state.showArchived;
    const btn = $('archivedToggle');
    btn.setAttribute('aria-pressed', String(state.showArchived));
    btn.textContent = state.showArchived ? 'Hide archived' : 'Show archived';
    loadHistory();
  });
  let searchTimer = null;
  $('historySearch').addEventListener('input', (e) => {
    state.historyQuery = e.target.value;
    // Debounced: typing a title should not fire one query per keystroke.
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadHistory, 220);
  });
  $('attachBtn').addEventListener('click', () => $('fileInput').click());
  $('fileInput').addEventListener('change', async (e) => {
    const files = [...(e.target.files || [])];
    e.target.value = '';                       // allow re-picking the same file
    if (files.length) await attachFiles(files);
  });
  $('webBtn').addEventListener('click', toggleWebSearch);

  document.querySelectorAll('.rail-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.rail-item').forEach((b) => {
        b.classList.toggle('is-active', b === btn);
        if (b === btn) b.setAttribute('aria-current', 'true');
        else b.removeAttribute('aria-current');
      });
      const view = btn.dataset.view;
      syncViewUrl(view);
      if (view === 'chat') { $('input').focus(); return; }
      openContext(true);
      const map = { chats: 'h-history', memory: 'h-memory', tools: 'h-tools', settings: 'h-providers' };
      const target = $(map[view]);
      if (target) target.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'start' });
    });
  });

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      $('modelTrigger')?.click();
    }
    if (e.key === 'Escape') {
      ui.closeActiveDialog();
      document.querySelector('.model-selector')?.classList.remove('is-open');
    }
  });

  window.addEventListener('beforeunload', (e) => {
    if (!state.streaming) return;
    e.preventDefault();
    e.returnValue = '';
  });

  $('voiceOrb').addEventListener('click', () => voice.toggle());
  $('voiceBtn').addEventListener('click', () => voice.toggle());
}

const voice = new VoiceController({
  onResult: (text) => { $('input').value = ''; send(text); },
  onInterim: (text) => { $('input').value = text; },
  onState: (s) => {
    setOrbListening(s === 'listening');
    // Voice turning off must not overwrite "thinking" while a reply is in
    // flight — only a genuinely idle session goes back to Ready.
    if (s !== 'listening' && !state.streaming) restCoreState();
  },
  onError: (msg) => ui.toast(msg, 'error'),
});

boot();
