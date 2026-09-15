(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { sessionId: null };
  const status = $('status');

  function setStatus(text, kind) { status.textContent = text; status.className = 'status' + (kind ? ' ' + kind : ''); }
  function addMessage(role, text) {
    const item = document.createElement('div'); item.className = 'message ' + role;
    item.textContent = text; $('messages').appendChild(item); item.scrollIntoView({ block: 'nearest' });
  }
  async function request(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error((await response.text()) || ('请求失败（' + response.status + '）'));
    return response.status === 204 ? null : response.json();
  }
  function sessionIdFrom(data) { return data && (data.session_id || data.sessionId || data.id); }
  async function refreshArtifacts() {
    if (!state.sessionId) return;
    const data = await request('/api/sessions/' + encodeURIComponent(state.sessionId) + '/artifacts');
    const list = $('artifacts'); list.replaceChildren();
    const artifacts = Array.isArray(data) ? data : (data.artifacts || []);
    if (!artifacts.length) { const empty = document.createElement('li'); empty.className = 'muted'; empty.textContent = '暂无产物'; list.appendChild(empty); return; }
    artifacts.forEach((artifact) => {
      const name = typeof artifact === 'string' ? artifact : (artifact.name || artifact.filename || '未命名产物');
      const item = document.createElement('li'); const link = document.createElement('a');
      link.textContent = name; link.download = name; link.href = '/api/sessions/' + encodeURIComponent(state.sessionId) + '/artifacts/' + encodeURIComponent(name);
      item.appendChild(link);
      if (artifact && artifact.verification) {
        const detail = document.createElement('span');
        const limitations = artifact.verification.limitations || [];
        detail.className = 'verification ' + artifact.verification.level;
        detail.textContent = ' · ' + artifact.verification.level + (limitations.length ? '（' + limitations.join('；') + '）' : '');
        item.appendChild(detail);
      }
      list.appendChild(item);
    });
  }
  $('session-form').addEventListener('submit', async (event) => {
    event.preventDefault(); setStatus('正在上传…', 'busy');
    try {
      const form = new FormData(); Array.from($('files').files).forEach((file) => form.append('files', file));
      const data = await request('/api/sessions', { method: 'POST', body: form });
      const id = sessionIdFrom(data); if (!id) throw new Error('响应中缺少会话 ID'); state.sessionId = String(id);
      $('message').disabled = $('send').disabled = false; $('messages').replaceChildren(); addMessage('assistant', '会话已创建：' + state.sessionId);
      await refreshArtifacts(); setStatus('会话已就绪');
    } catch (error) { setStatus(error.message, 'error'); }
  });
  $('message-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const input = $('message'); const message = input.value.trim(); if (!message || !state.sessionId) return;
    addMessage('user', message); input.value = ''; input.disabled = $('send').disabled = true; setStatus('正在处理…', 'busy');
    try {
      const data = await request('/api/sessions/' + encodeURIComponent(state.sessionId) + '/messages', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message }) });
      const reply = data && (data.message || data.response || data.content || data.text); if (reply) addMessage('assistant', String(reply));
      await refreshArtifacts(); setStatus('会话已就绪');
    } catch (error) { addMessage('assistant', '错误：' + error.message); setStatus(error.message, 'error'); }
    finally { input.disabled = $('send').disabled = false; input.focus(); }
  });
}());
