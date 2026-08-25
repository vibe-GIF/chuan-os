/* 川流 HUD PWA 客户端：SCENE 协议走 WebSocket（与 Flutter 悬浮层同一套帧）。 */
(function () {
  'use strict';

  var connDot = document.getElementById('connDot');
  var connText = document.getElementById('connText');
  var agentSel = document.getElementById('agentSel');
  var orb = document.getElementById('orb');
  var effectLabel = document.getElementById('effectLabel');
  var logEl = document.getElementById('log');
  var msgEl = document.getElementById('msg');
  var sendBtn = document.getElementById('send');

  var state = { agent: 'jarvis', effect: 'idle', user: { text: '', ts: '' }, ai: { text: '', ts: '' } };
  var history = [];
  var lastUserTs = '', lastAiTs = '';
  var ws = null, wsTimer = null;
  var wsUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws';

  function setConn(ok, text) {
    connDot.classList.toggle('on', !!ok);
    connText.textContent = text;
  }

  function connect() {
    clearTimeout(wsTimer);
    setConn(false, '连接中…');
    try {
      ws = new WebSocket(wsUrl);
    } catch (e) { ws = null; }
    if (!ws) { wsTimer = setTimeout(connect, 3000); return; }
    ws.onopen = function () { setConn(true, '已连接'); };
    ws.onclose = function () { setConn(false, '重连中…'); wsTimer = setTimeout(connect, 3000); };
    ws.onerror = function () { try { ws.close(); } catch (e) {} };
    ws.onmessage = function (ev) { try { handleLine(ev.data); } catch (err) {} };
  }

  function handleLine(line) {
    var idx = line.indexOf(':');
    if (idx <= 0) return;
    var type = line.slice(0, idx).trim();
    var raw = line.slice(idx + 1);
    if (type === 'hello' || type === 'scene' || type === 'patch') {
      var p;
      try { p = JSON.parse(raw); } catch (e) { return; }
      if (type === 'hello' && ws && ws.readyState === 1) { try { ws.send('welcome'); } catch (e) {} }
      if (type === 'scene') applyAll(p);
      if (type === 'patch') applyPatch(p);
    }
  }

  function applyAll(s) {
    state.agent = s.agent || state.agent;
    state.effect = s.effect || state.effect;
    state.user = s.user || state.user;
    state.ai = s.ai || state.ai;
    history = [];
    lastUserTs = ''; lastAiTs = '';
    if (state.user.text && state.user.ts) { history.push({ role: 'user', text: state.user.text }); lastUserTs = state.user.ts; }
    if (state.ai.text && state.ai.ts) { history.push({ role: 'ai', text: state.ai.text }); lastAiTs = state.ai.ts; }
    if (!history.length) history.push({ role: 'hint', text: '已连接二级流（SCENE v1）' });
    renderHud();
    renderLog();
  }

  function applyPatch(p) {
    if (p.agent) state.agent = p.agent;
    if (p.effect) state.effect = p.effect;
    if (p.user) {
      state.user = p.user;
      if (p.user.ts && p.user.ts !== lastUserTs) { lastUserTs = p.user.ts; if (p.user.text) { history.push({ role: 'user', text: p.user.text }); renderLog(); } }
    }
    if (p.ai) {
      state.ai = p.ai;
      if (p.ai.ts && p.ai.ts !== lastAiTs) { lastAiTs = p.ai.ts; if (p.ai.text) { history.push({ role: 'ai', text: p.ai.text }); renderLog(); } }
    }
    renderHud();
  }

  function renderHud() {
    effectLabel.textContent = state.effect;
    orb.className = 'orb ' + String(state.effect).replace(/[^a-zA-Z0-9_-]/g, '');
    if (agentSel.value !== state.agent) agentSel.value = state.agent;
  }

  function renderLog() {
    logEl.innerHTML = '';
    history.forEach(function (m) {
      var d = document.createElement('div');
      d.className = 'line ' + m.role;
      d.textContent = (m.role === 'user' ? '我：' : (m.role === 'ai' ? '川流：' : '')) + m.text;
      logEl.appendChild(d);
    });
    logEl.scrollTop = logEl.scrollHeight;
  }

  function sendMessage() {
    var text = msgEl.value.trim();
    if (!text) return;
    msgEl.value = '';
    if (ws && ws.readyState === 1) {
      try { ws.send('message:' + text); } catch (e) {}
    } else {
      history.push({ role: 'hint', text: '未连接，请稍候重试' });
      renderLog();
    }
  }

  function hud(command, body) {
    var payload = Object.assign({ command: command }, body || {});
    fetch('/api/hud', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).catch(function () {});
  }

  sendBtn.addEventListener('click', sendMessage);
  msgEl.addEventListener('keydown', function (e) { if (e.key === 'Enter') sendMessage(); });
  agentSel.addEventListener('change', function () { hud('agent', { agent: agentSel.value }); });

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(function () {});
  }
  connect();
})();