/**
 * static/app.js — Preventify chat frontend
 *
 * Responsibilities:
 *   - Generate/persist user_id in localStorage; new session_id per page load
 *   - POST /chat with message, receive SSE stream
 *   - Render: status events, chunk events (word-by-word), clarify buttons, done metadata
 *   - Update debug panel after each turn
 *   - Handle errors and rate limit responses
 */

'use strict';

// ── Identity ─────────────────────────────────────────────────────────────────

let userId = localStorage.getItem('preventify_user_id');
if (!userId) {
  userId = crypto.randomUUID();
  localStorage.setItem('preventify_user_id', userId);
}

// Fresh session every page load — no history shown (per design doc Section 10)
const sessionId = crypto.randomUUID();

// ── DOM refs ──────────────────────────────────────────────────────────────────

const chatArea     = document.getElementById('chatArea');
const messageInput = document.getElementById('messageInput');
const sendBtn      = document.getElementById('sendBtn');
const debugContent = document.getElementById('debugContent');
const debugToggle  = document.getElementById('debugToggle');

// Debug value elements
const dbgQds      = document.getElementById('dbgQds');
const dbgSources  = document.getElementById('dbgSources');
const dbgRisk     = document.getElementById('dbgRisk');
const dbgFallback = document.getElementById('dbgFallback');

// ── State ─────────────────────────────────────────────────────────────────────

let isStreaming = false;

// ── Debug panel toggle ────────────────────────────────────────────────────────

function toggleDebug() {
  const content = debugContent;
  if (content.style.display === 'none') {
    content.style.display = 'grid';
    debugToggle.textContent = '▲ Debug panel';
  } else {
    content.style.display = 'none';
    debugToggle.textContent = '▼ Debug panel';
  }
}

// ── Bubble helpers ────────────────────────────────────────────────────────────

function addBubble(cls, text) {
  const row = document.createElement('div');
  row.className = `bubble ${cls}`;
  if (text) row.textContent = text;
  chatArea.appendChild(row);
  scrollToBottom();
  return row;
}

function addStatusBubble(text) {
  const el = document.createElement('div');
  el.className = 'bubble status-bubble';
  el.textContent = text;
  chatArea.appendChild(el);
  scrollToBottom();
  return el;
}

function scrollToBottom() {
  chatArea.scrollTop = chatArea.scrollHeight;
}

// ── Input helpers ─────────────────────────────────────────────────────────────

function setInputEnabled(enabled) {
  messageInput.disabled = !enabled;
  sendBtn.disabled = !enabled;
  isStreaming = !enabled;
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

// ── Debug panel update ────────────────────────────────────────────────────────

function updateDebug(meta) {
  if (!meta) return;

  // QDS + intent
  const qds    = meta.qds_score != null ? meta.qds_score : '—';
  const intent = meta.intent || '—';
  dbgQds.textContent = `${qds} / ${intent}`;

  // Sources
  if (meta.sources && meta.sources.length > 0) {
    dbgSources.textContent = meta.sources
      .map(s => `${s.source} §${s.section || '?'} [G${s.grade}]`)
      .join(', ');
  } else {
    dbgSources.textContent = 'none (no RAG)';
  }

  // Risk tier
  dbgRisk.textContent = `Tier ${meta.risk_tier ?? 0}`;
  dbgRisk.className = 'debug-value' + (meta.risk_tier >= 3 ? ' warn' : '');

  // Fallback flags
  const fallbacks = [];
  if (meta.phase1_fallback) fallbacks.push('Phase 1');
  if (meta.phase2_fallback) fallbacks.push('Phase 2');
  if (meta.constraint_violation) fallbacks.push('⚠ constraint');
  dbgFallback.textContent = fallbacks.length ? fallbacks.join(', ') : 'none';
  dbgFallback.className = 'debug-value' + (fallbacks.length ? ' warn' : ' ok');

  // Cache hit indicator
  if (meta.query_cache_hit) {
    dbgSources.textContent += ' (cache hit)';
  }
}

// ── Clarify options rendering ─────────────────────────────────────────────────

function renderClarifyOptions(bubble, options, question) {
  if (!options || options.length === 0) return;

  const row = document.createElement('div');
  row.className = 'clarify-options';

  options.forEach(opt => {
    const btn = document.createElement('button');
    btn.className = 'clarify-btn';
    btn.textContent = opt;
    btn.onclick = () => {
      // Disable all buttons after selection
      row.querySelectorAll('.clarify-btn').forEach(b => b.disabled = true);
      // Send selected option as next message
      sendMessage(opt);
    };
    row.appendChild(btn);
  });

  bubble.appendChild(row);
  scrollToBottom();
}

// ── Main send logic ───────────────────────────────────────────────────────────

async function sendMessage(overrideText) {
  const text = (overrideText || messageInput.value).trim();
  if (!text || isStreaming) return;

  // Clear input
  if (!overrideText) {
    messageInput.value = '';
    messageInput.style.height = 'auto';
  }

  // Show user bubble
  addBubble('user-bubble', text);

  // Disable input during streaming
  setInputEnabled(false);

  // Create bot bubble (will accumulate chunks)
  let statusEl = addStatusBubble('Looking up your question...');
  let botBubble = null;
  let clarifyBubble = null;
  let responseText = '';
  let gotChunk = false;

  try {
    const resp = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message:    text,
        user_id:    userId,
        session_id: sessionId,
      }),
    });

    if (!resp.ok && resp.status === 429) {
      // Rate limit
      statusEl.remove();
      addBubble('bot-bubble', "You've sent too many messages. Please wait a few minutes.");
      setInputEnabled(true);
      return;
    }

    if (!resp.body) throw new Error('No response body');

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // keep incomplete line in buffer

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;

        let event;
        try { event = JSON.parse(raw); }
        catch { continue; }

        switch (event.type) {

          case 'status':
            if (statusEl) {
              statusEl.textContent = event.text;
            } else {
              statusEl = addStatusBubble(event.text);
            }
            break;

          case 'chunk':
            if (!gotChunk) {
              // First chunk — replace status with real bot bubble
              if (statusEl) { statusEl.remove(); statusEl = null; }
              botBubble = addBubble('bot-bubble', '');
              gotChunk = true;
            }
            responseText += event.text;
            if (botBubble) {
              botBubble.textContent = responseText;
              scrollToBottom();
            }
            break;

          case 'clarify':
            if (statusEl) { statusEl.remove(); statusEl = null; }
            clarifyBubble = addBubble('bot-bubble', event.question || '');
            if (event.format !== 'open') {
              renderClarifyOptions(clarifyBubble, event.options, event.question);
            }
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
