'use strict';

// ---------------------------------------------------------------------------
// Identity
// ---------------------------------------------------------------------------

let userId = localStorage.getItem('preventify_user_id');
if (!userId) {
  userId = crypto.randomUUID();
  localStorage.setItem('preventify_user_id', userId);
}
const sessionId = crypto.randomUUID();

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const chatArea       = document.getElementById('chatArea');
const compareArea    = document.getElementById('compareArea');
const messageInput   = document.getElementById('messageInput');
const sendBtn        = document.getElementById('sendBtn');
const debugContent   = document.getElementById('debugContent');
const debugToggle    = document.getElementById('debugToggle');
const chatModeBtn    = document.getElementById('chatModeBtn');
const compareModeBtn = document.getElementById('compareModeBtn');

const dbgQds          = document.getElementById('dbgQds');
const dbgSources      = document.getElementById('dbgSources');
const dbgRisk         = document.getElementById('dbgRisk');
const dbgFallback     = document.getElementById('dbgFallback');
const dbgTimingsTotal = document.getElementById('dbgTimingsTotal');
const dbgTimingsChart = document.getElementById('dbgTimingsChart');

// ---------------------------------------------------------------------------
// Mode state
// ---------------------------------------------------------------------------

let currentMode = 'chat';   // 'chat' | 'compare'
let isStreaming  = false;

function setMode(mode) {
  currentMode = mode;
  if (mode === 'chat') {
    chatArea.style.display    = 'flex';
    compareArea.style.display = 'none';
    chatModeBtn.classList.add('active');
    compareModeBtn.classList.remove('active');
    messageInput.placeholder = 'Type your question...';
  } else {
    chatArea.style.display    = 'none';
    compareArea.style.display = 'block';
    chatModeBtn.classList.remove('active');
    compareModeBtn.classList.add('active');
    messageInput.placeholder = 'Type a question to compare all models...';
  }
}

// ---------------------------------------------------------------------------
// Debug panel
// ---------------------------------------------------------------------------

function _syncDebugHeight() {
  // Measure the panel's rendered height and push it into --debug-h so the
  // chat area's bottom offset stays accurate when the panel opens/closes.
  requestAnimationFrame(() => {
    const h = document.getElementById('debugPanel').offsetHeight;
    document.documentElement.style.setProperty('--debug-h', h + 'px');
  });
}

function toggleDebug() {
  if (debugContent.style.display === 'none') {
    debugContent.style.display = 'grid';
    debugToggle.textContent = '▲ Debug panel';
  } else {
    debugContent.style.display = 'none';
    debugToggle.textContent = '▼ Debug panel';
  }
  _syncDebugHeight();
}

function _openDebugIfClosed() {
  if (debugContent.style.display === 'none') {
    debugContent.style.display = 'grid';
    debugToggle.textContent = '▲ Debug panel';
    _syncDebugHeight();
  }
}

function updateDebug(meta) {
  if (!meta) return;
  _openDebugIfClosed();
  const qds    = meta.qds_score != null ? meta.qds_score : '—';
  const intent = meta.intent || '—';
  dbgQds.textContent = `${qds} / ${intent}`;

  if (meta.sources && meta.sources.length > 0) {
    dbgSources.textContent = meta.sources
      .map(s => `${s.source} §${s.section || '?'} [G${s.grade}]`)
      .join(', ');
  } else {
    dbgSources.textContent = 'none (no RAG)';
  }

  dbgRisk.textContent = `Tier ${meta.risk_tier ?? 0}`;
  dbgRisk.className   = 'debug-value' + (meta.risk_tier >= 3 ? ' warn' : '');

  const fallbacks = [];
  if (meta.phase1_fallback)      fallbacks.push('Phase 1');
  if (meta.phase2_fallback)      fallbacks.push('Phase 2');
  if (meta.constraint_violation) fallbacks.push('constraint');
  dbgFallback.textContent = fallbacks.length ? fallbacks.join(', ') : 'none';
  dbgFallback.className   = 'debug-value' + (fallbacks.length ? ' warn' : ' ok');

  if (meta.query_cache_hit) dbgSources.textContent += ' (cache hit)';

  // Timing breakdown — keys must match phase2_runner.py _t dict exactly
  const t = meta.timings || {};
  const STEPS = [
    { key: 'query_build_ms', label: 'Query build',   barClass: ''           },
    { key: 'cache_check_ms', label: 'Cache check',   barClass: ''           },
    { key: 'embed_ms',       label: 'Embed',          barClass: ''           },
    { key: 'ann_search_ms',  label: 'ANN search',    barClass: ''           },
    { key: 'rerank_ms',      label: 'Rerank',         barClass: 'bar-rerank' },
    { key: 'llm_ms',         label: 'LLM generate',  barClass: 'bar-llm'    },
  ];

  const totalMs = t.total_ms || 0;
  dbgTimingsChart.innerHTML = '';

  if (totalMs <= 0) {
    dbgTimingsTotal.textContent = '—';
    return;
  }

  dbgTimingsTotal.textContent = `${totalMs} ms total`;

  // Use sum of known steps as the bar scale so bars are proportional to each other.
  // This avoids the "bars don't add up" problem when total_ms includes unmeasured overhead.
  const stepSum = STEPS.reduce((acc, s) => acc + (t[s.key] || 0), 0);
  const barBase = stepSum > 0 ? stepSum : totalMs;
  const CACHE_SKIP_KEYS = new Set(['embed_ms', 'ann_search_ms']);

  STEPS.forEach(({ key, label, barClass }) => {
    const ms = t[key];
    if (ms == null) return;   // key absent — step didn't run at all

    const pct    = Math.round((ms / barBase) * 100);
    const msText = ms === 0
      ? (CACHE_SKIP_KEYS.has(key) && meta.query_cache_hit ? 'cache hit' : '<1 ms')
      : `${ms} ms`;

    const row = document.createElement('div');
    row.className = 'timing-row';

    const labelEl = document.createElement('span');
    labelEl.className   = 'timing-label';
    labelEl.textContent = label;

    const barWrap = document.createElement('div');
    barWrap.className = 'timing-bar-wrap';

    const bar = document.createElement('div');
    bar.className = 'timing-bar' + (barClass ? ' ' + barClass : '') + (ms === 0 ? ' bar-zero' : '');
    bar.style.width = ms === 0 ? '0%' : Math.max(pct, 1) + '%';

    const msEl = document.createElement('span');
    msEl.className   = 'timing-ms' + (ms === 0 ? ' timing-skipped' : '');
    msEl.textContent = msText;

    barWrap.appendChild(bar);
    row.appendChild(labelEl);
    row.appendChild(barWrap);
    row.appendChild(msEl);
    dbgTimingsChart.appendChild(row);
  });

  _syncDebugHeight();
}

// ---------------------------------------------------------------------------
// Chat mode helpers
// ---------------------------------------------------------------------------

function addBubble(cls, text) {
  const row = document.createElement('div');
  row.className = `bubble ${cls}`;
  if (text) row.textContent = text;
  chatArea.appendChild(row);
  scrollChat();
  return row;
}

function addStatusBubble(text) {
  const el = document.createElement('div');
  el.className   = 'bubble status-bubble';
  el.textContent = text;
  chatArea.appendChild(el);
  scrollChat();
  return el;
}

function scrollChat() {
  chatArea.scrollTop = chatArea.scrollHeight;
}

function renderClarifyOptions(bubble, options) {
  if (!options || options.length === 0) return;
  const row = document.createElement('div');
  row.className = 'clarify-options';
  options.forEach(opt => {
    const btn = document.createElement('button');
    btn.className  = 'clarify-btn';
    btn.textContent = opt;
    btn.onclick = () => {
      row.querySelectorAll('.clarify-btn').forEach(b => b.disabled = true);
      sendMessage(opt);
    };
    row.appendChild(btn);
  });
  bubble.appendChild(row);
  scrollChat();
}

// ---------------------------------------------------------------------------
// Input helpers (shared)
// ---------------------------------------------------------------------------

function setInputEnabled(enabled) {
  messageInput.disabled = !enabled;
  sendBtn.disabled      = !enabled;
  isStreaming           = !enabled;
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!isStreaming) sendMessage();
  }
}

// ---------------------------------------------------------------------------
// Unified send dispatcher
// ---------------------------------------------------------------------------

async function sendMessage(overrideText) {
  const text = (overrideText || messageInput.value).trim();
  if (!text || isStreaming) return;

  if (!overrideText) {
    messageInput.value      = '';
    messageInput.style.height = 'auto';
  }

  if (currentMode === 'compare') {
    await sendCompare(text);
  } else {
    await sendChat(text);
  }
}

// ---------------------------------------------------------------------------
// Chat mode send
// ---------------------------------------------------------------------------

async function sendChat(text) {
  addBubble('user-bubble', text);
  setInputEnabled(false);

  let statusEl    = addStatusBubble('Looking up your question...');
  let botBubble   = null;
  let responseText = '';
  let gotChunk    = false;

  try {
    const resp = await fetch('/chat', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ message: text, user_id: userId, session_id: sessionId }),
    });

    if (!resp.ok && resp.status === 429) {
      statusEl.remove();
      addBubble('bot-bubble', "You've sent too many messages. Please wait a few minutes.");
      setInputEnabled(true);
      return;
    }
    if (!resp.body) throw new Error('No response body');

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;
        let event;
        try { event = JSON.parse(raw); } catch { continue; }

        switch (event.type) {
          case 'status':
            if (statusEl) statusEl.textContent = event.text;
            else          statusEl = addStatusBubble(event.text);
            break;

          case 'chunk':
            if (!gotChunk) {
              if (statusEl) { statusEl.remove(); statusEl = null; }
              botBubble = addBubble('bot-bubble', '');
              gotChunk  = true;
            }
            responseText     += event.text;
            botBubble.textContent = responseText;
            scrollChat();
            break;

          case 'clarify':
            if (statusEl) { statusEl.remove(); statusEl = null; }
            const clarifyBubble = addBubble('bot-bubble', event.question || '');
            if (event.format !== 'open') renderClarifyOptions(clarifyBubble, event.options);
            break;

          case 'error':
            if (statusEl) { statusEl.remove(); statusEl = null; }
            addBubble('bot-bubble', event.text || 'Something went wrong. Please try again.');
            break;

          case 'done':
            if (statusEl) { statusEl.remove(); statusEl = null; }
            if (event.meta) updateDebug(event.meta);
            break;
        }
      }
    }
  } catch (err) {
    if (statusEl) statusEl.remove();
    console.error('chat error:', err);
    addBubble('bot-bubble', 'Connection error. Please check your internet and try again.');
  } finally {
    setInputEnabled(true);
    messageInput.focus();
  }
}

// ---------------------------------------------------------------------------
// Compare mode send
// ---------------------------------------------------------------------------

const _PROVIDER_LABELS = {
  groq:       'GROQ',
  cerebras:   'CEREBRAS',
  openrouter: 'OPENROUTER',
};

function _providerLabel(provider) {
  return _PROVIDER_LABELS[provider] || provider.toUpperCase();
}

function _subProviderLabel(provider, modelId) {
  if (provider !== 'openrouter') return null;
  const id = modelId.toLowerCase();
  if (id.startsWith('google/'))     return 'Gemini';
  if (id.startsWith('openai/'))     return 'GPT';
  if (id.startsWith('anthropic/'))  return 'Claude';
  if (id.startsWith('x-ai/'))       return 'Grok';
  if (id.startsWith('deepseek/'))   return 'DeepSeek';
  return null;
}

function _createCompareCard(provider, model) {
  const card = document.createElement('div');
  card.className = 'compare-card loading';
  card.id = 'card-' + provider + '-' + model.replace(/\//g, '-');

  const header = document.createElement('div');
  header.className = 'card-header';

  const badge = document.createElement('span');
  badge.className   = 'provider-badge badge-' + provider;
  badge.textContent = _providerLabel(provider);

  const sub = _subProviderLabel(provider, model);
  if (sub) {
    const subBadge = document.createElement('span');
    subBadge.className   = 'provider-sub-label';
    subBadge.textContent = sub;
    header.appendChild(badge);
    header.appendChild(subBadge);
  } else {
    header.appendChild(badge);
  }

  // Strip the org prefix (e.g. "google/") for display — keeps card compact
  const displayModel = model.includes('/') ? model.split('/').slice(1).join('/') : model;
  const modelName = document.createElement('span');
  modelName.className   = 'card-model-name';
  modelName.textContent = displayModel;

  header.appendChild(modelName);

  const spinner = document.createElement('div');
  spinner.className = 'card-spinner';
  spinner.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';

  card.appendChild(header);
  card.appendChild(spinner);
  return card;
}

function _fillCompareCard(card, result) {
  card.classList.remove('loading');

  // Remove spinner
  const spinner = card.querySelector('.card-spinner');
  if (spinner) spinner.remove();

  const meta = document.createElement('div');
  meta.className   = 'card-meta';
  meta.textContent = `${result.latency_s}s · ${result.output_tokens} tokens`;

  const body = document.createElement('div');
  body.className   = 'card-text';
  body.textContent = result.text;

  card.appendChild(meta);
  card.appendChild(body);
}

async function sendCompare(text) {
  setInputEnabled(false);

  // Clear previous results
  compareArea.innerHTML = '';

  // Show query at top
  const queryEl = document.createElement('div');
  queryEl.className   = 'compare-query';
  queryEl.textContent = text;
  compareArea.appendChild(queryEl);

  // Status line
  const statusEl = document.createElement('div');
  statusEl.className   = 'compare-status';
  statusEl.textContent = 'Connecting to models...';
  compareArea.appendChild(statusEl);

  // Card grid
  const grid = document.createElement('div');
  grid.className = 'compare-grid';
  compareArea.appendChild(grid);

  const pendingCards = {};   // model id -> card element
  let totalModels = 0;
  let doneCount   = 0;

  try {
    const resp = await fetch('/compare', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ message: text }),
    });
    if (!resp.body) throw new Error('No response body');

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;
        let event;
        try { event = JSON.parse(raw); } catch { continue; }

        switch (event.type) {

          case 'compare_start':
            totalModels = event.total;
            statusEl.textContent = `Running ${totalModels} models in parallel...`;
            // Pre-create placeholder cards (we don't know model names yet,
            // so cards will be added as results arrive instead)
            break;

          case 'model_result':
            doneCount++;
            statusEl.textContent = `${doneCount} / ${totalModels} responded`;
            const card = _createCompareCard(event.provider, event.model);
            grid.appendChild(card);
            // Fill immediately (result came with the event)
            _fillCompareCard(card, event);
            compareArea.scrollTop = compareArea.scrollHeight;
            break;

          case 'compare_done':
            statusEl.textContent = `${event.total} model${event.total !== 1 ? 's' : ''} responded`;
            statusEl.classList.add('done');
            break;

          case 'error':
            statusEl.textContent = event.text || 'Error running comparison.';
            statusEl.classList.add('error');
            break;
        }
      }
    }
  } catch (err) {
    console.error('compare error:', err);
    statusEl.textContent = 'Connection error. Please try again.';
    statusEl.classList.add('error');
  } finally {
    setInputEnabled(true);
    messageInput.focus();
  }
}
