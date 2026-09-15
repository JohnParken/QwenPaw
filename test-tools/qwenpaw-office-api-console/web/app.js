(function () {
  'use strict';

  const API = '/office-api/api/v1';
  const $ = (id) => document.getElementById(id);
  const state = {
    sessionId: null,
    files: [],
    activeRequestId: null,
    activeController: null,
    assistantNode: null
  };

  function requestId(prefix) {
    const value = globalThis.crypto && crypto.randomUUID
      ? crypto.randomUUID()
      : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
    return prefix + '-' + value;
  }

  function identityHeaders(id) {
    return {
      'X-Tenant-Id': $('tenant-id').value.trim(),
      'X-User-Id': $('user-id').value.trim(),
      'X-Request-Id': id || requestId('ui')
    };
  }

  function saveIdentity() {
    localStorage.setItem('qwenpaw-office-tenant', $('tenant-id').value.trim());
    localStorage.setItem('qwenpaw-office-user', $('user-id').value.trim());
  }

  function setStatus(text, kind) {
    $('status').textContent = text;
    $('status').dataset.kind = kind || '';
  }

  function errorDetail(payload, fallback) {
    if (!payload) return fallback;
    if (typeof payload === 'string') return payload;
    return payload.detail || payload.message || fallback;
  }

  async function parseError(response) {
    const text = await response.text();
    try { return errorDetail(JSON.parse(text), text || response.statusText); }
    catch (_) { return text || response.statusText; }
  }

  async function api(path, options) {
    const response = await fetch(API + path, options || {});
    if (!response.ok) throw new Error(await parseError(response));
    if (response.status === 204) return null;
    return response.json();
  }

  function resetSession() {
    state.sessionId = null;
    state.files = [];
    $('session-id').value = '';
    $('file-picker').value = '';
    $('message').disabled = true;
    $('send-message').disabled = true;
    $('upload-files').disabled = true;
    $('close-session').disabled = true;
    $('refresh-artifacts').disabled = true;
    renderFiles();
    $('artifacts').innerHTML = '<p class="empty">暂无产物</p>';
  }

  function setSession(sessionId) {
    state.sessionId = sessionId;
    $('session-id').value = sessionId;
    $('message').disabled = false;
    $('send-message').disabled = false;
    $('upload-files').disabled = false;
    $('close-session').disabled = false;
    $('refresh-artifacts').disabled = false;
  }

  function addMessage(role, text, extraClass) {
    const welcome = $('messages').querySelector('.welcome');
    if (welcome) welcome.remove();
    const node = document.createElement('div');
    node.className = 'message ' + role + (extraClass ? ' ' + extraClass : '');
    node.textContent = text;
    $('messages').appendChild(node);
    node.scrollIntoView({ block: 'nearest' });
    return node;
  }

  function formatBytes(size) {
    if (!Number.isFinite(Number(size))) return '';
    if (size < 1024) return size + ' B';
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KiB';
    return (size / 1024 / 1024).toFixed(1) + ' MiB';
  }

  function renderFiles() {
    const root = $('files');
    root.replaceChildren();
    if (!state.files.length) {
      const empty = document.createElement('p');
      empty.className = 'empty';
      empty.textContent = '暂无文件';
      root.appendChild(empty);
      $('attachment-count').textContent = '0 个附件';
      return;
    }
    state.files.forEach((file) => {
      const label = document.createElement('label');
      label.className = 'file-card';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = file.selected;
      checkbox.addEventListener('change', () => {
        file.selected = checkbox.checked;
        updateAttachmentCount();
      });
      const text = document.createElement('span');
      const name = document.createElement('strong');
      name.textContent = file.filename || file.file_id;
      const detail = document.createElement('small');
      detail.textContent = formatBytes(file.size) + ' · ' + (file.content_type || 'unknown');
      text.append(name, detail);
      label.append(checkbox, text);
      root.appendChild(label);
    });
    updateAttachmentCount();
  }

  function updateAttachmentCount() {
    const count = state.files.filter((item) => item.selected).length;
    $('attachment-count').textContent = count + ' 个附件';
  }

  function appendEvent(name, data) {
    const list = $('events');
    const placeholder = list.querySelector('.muted');
    if (placeholder) placeholder.remove();
    const item = document.createElement('li');
    const title = document.createElement('span');
    title.className = 'event-name';
    title.textContent = name + ' ';
    item.appendChild(title);
    item.appendChild(document.createTextNode(JSON.stringify(data || {})));
    list.appendChild(item);
    list.scrollTop = list.scrollHeight;
  }

  async function checkHealth() {
    const badge = $('health-badge');
    badge.className = 'badge neutral';
    badge.textContent = '检查中';
    try {
      const live = await api('/health/live');
      const readyResponse = await fetch(API + '/health/ready');
      const ready = await readyResponse.json();
      badge.className = 'badge ' + (ready.ready ? 'ready' : 'failed');
      badge.textContent = ready.ready ? 'Ready' : 'Not ready';
      setStatus(ready.ready ? 'Office API 已就绪' : '服务存活，但 readiness 未通过', ready.ready ? 'ok' : 'warning');
      if (!live.live) throw new Error('liveness 响应异常');
      const skills = await api('/skills', { headers: identityHeaders(requestId('skills')) });
      renderSkills(skills.skills || {});
      if (!readyResponse.ok) appendEvent('readiness.failed', { failures: ready.failures || [] });
    } catch (error) {
      badge.className = 'badge failed';
      badge.textContent = '不可用';
      setStatus(error.message, 'error');
    }
  }

  function renderSkills(skills) {
    const root = $('skills');
    root.replaceChildren();
    Object.entries(skills).forEach(([name, value]) => {
      const item = document.createElement('li');
      const status = document.createElement('span');
      status.className = value.ready ? 'ok' : 'bad';
      status.textContent = value.ready ? 'ready' : 'failed';
      item.append(document.createTextNode(name), status);
      root.appendChild(item);
    });
    if (!root.children.length) root.innerHTML = '<li class="muted">未返回 Skill</li>';
  }

  async function createSession() {
    saveIdentity();
    setStatus('正在创建 Session…');
    try {
      const data = await api('/sessions', {
        method: 'POST',
        headers: { ...identityHeaders(requestId('session')), 'Content-Type': 'application/json' },
        body: JSON.stringify({ metadata: { source: 'native-test-console' } })
      });
      setSession(data.session_id);
      addMessage('assistant', 'Session 已创建：' + data.session_id);
      setStatus('Session 已就绪');
      await refreshArtifacts();
    } catch (error) { setStatus(error.message, 'error'); }
  }

  async function closeSession() {
    if (!state.sessionId) return;
    try {
      await api('/sessions/' + encodeURIComponent(state.sessionId), {
        method: 'DELETE', headers: identityHeaders(requestId('close'))
      });
      addMessage('assistant', 'Session 已关闭');
      resetSession();
      setStatus('Session 已关闭');
    } catch (error) { setStatus(error.message, 'error'); }
  }

  async function uploadFiles() {
    const selected = Array.from($('file-picker').files || []);
    if (!state.sessionId || !selected.length) return;
    $('upload-files').disabled = true;
    setStatus('正在上传 ' + selected.length + ' 个文件…');
    try {
      for (const file of selected) {
        const data = await api('/files', {
          method: 'POST',
          headers: {
            ...identityHeaders(requestId('upload')),
            'X-File-Name': file.name,
            'X-Session-Id': state.sessionId,
            'Content-Type': file.type || 'application/octet-stream'
          },
          body: file
        });
        state.files.push({ ...data, selected: true });
      }
      $('file-picker').value = '';
      renderFiles();
      setStatus('文件上传完成');
    } catch (error) { setStatus(error.message, 'error'); }
    finally { $('upload-files').disabled = !state.sessionId; }
  }

  function handleSseEvent(payload) {
    appendEvent(payload.event, payload.data);
    if (payload.event === 'message.delta') {
      if (!state.assistantNode) state.assistantNode = addMessage('assistant', '');
      state.assistantNode.textContent += payload.data.delta || '';
    } else if (payload.event === 'turn.completed') {
      if (!state.assistantNode && payload.data.message) addMessage('assistant', payload.data.message);
    } else if (payload.event === 'turn.failed') {
      addMessage('assistant', errorDetail(payload.data, '执行失败'), 'error');
    }
  }

  async function consumeSse(response) {
    if (!response.body) throw new Error('浏览器不支持流式响应');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      buffer += decoder.decode(part.value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || '';
      for (const block of blocks) {
        const dataLines = block.split(/\r?\n/)
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart());
        if (dataLines.length) handleSseEvent(JSON.parse(dataLines.join('\n')));
      }
    }
  }

  async function sendMessage(event) {
    event.preventDefault();
    const content = $('message').value.trim();
    if (!state.sessionId || !content || state.activeRequestId) return;
    addMessage('user', content);
    $('message').value = '';
    state.assistantNode = null;
    state.activeRequestId = requestId('turn');
    state.activeController = new AbortController();
    $('send-message').disabled = true;
    $('cancel-turn').disabled = false;
    setStatus('Agent 正在执行…');
    const body = {
      content,
      file_ids: state.files.filter((item) => item.selected).map((item) => item.file_id),
      provider: $('provider').value
    };
    try {
      const mode = $('response-mode').value;
      const response = await fetch(API + '/sessions/' + encodeURIComponent(state.sessionId) + '/messages', {
        method: 'POST',
        headers: {
          ...identityHeaders(state.activeRequestId),
          'Content-Type': 'application/json',
          'Accept': mode === 'sse' ? 'text/event-stream' : 'application/json'
        },
        body: JSON.stringify(body),
        signal: state.activeController.signal
      });
      if (!response.ok) throw new Error(await parseError(response));
      if (mode === 'sse') await consumeSse(response);
      else {
        const result = await response.json();
        addMessage('assistant', result.message || '(无文本响应)');
        appendEvent('turn.' + result.status, result);
      }
      setStatus('执行完成');
      await refreshArtifacts();
    } catch (error) {
      if (error.name === 'AbortError') setStatus('执行已取消', 'warning');
      else {
        addMessage('assistant', '错误：' + error.message, 'error');
        setStatus(error.message, 'error');
      }
    } finally {
      state.activeRequestId = null;
      state.activeController = null;
      state.assistantNode = null;
      $('send-message').disabled = !state.sessionId;
      $('cancel-turn').disabled = true;
      $('message').focus();
    }
  }

  async function cancelTurn() {
    if (!state.sessionId || !state.activeRequestId) return;
    const executingRequest = state.activeRequestId;
    try {
      await api('/sessions/' + encodeURIComponent(state.sessionId) + '/cancel', {
        method: 'POST',
        headers: { ...identityHeaders(requestId('cancel')), 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: executingRequest })
      });
      if (state.activeController) state.activeController.abort();
    } catch (error) { setStatus(error.message, 'error'); }
  }

  async function refreshArtifacts() {
    if (!state.sessionId) return;
    try {
      const artifacts = await api('/sessions/' + encodeURIComponent(state.sessionId) + '/artifacts', {
        headers: identityHeaders(requestId('artifacts'))
      });
      renderArtifacts(artifacts);
    } catch (error) { setStatus(error.message, 'error'); }
  }

  function renderArtifacts(artifacts) {
    const root = $('artifacts');
    root.replaceChildren();
    if (!artifacts.length) {
      root.innerHTML = '<p class="empty">暂无产物</p>';
      return;
    }
    artifacts.forEach((artifact) => {
      const row = document.createElement('div');
      row.className = 'artifact';
      const text = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = artifact.filename || artifact.title || artifact.artifact_id;
      const detail = document.createElement('small');
      detail.textContent = formatBytes(artifact.size) + (artifact.verified ? ' · 已验证' : ' · 未验证');
      text.append(name, detail);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'secondary';
      button.textContent = '下载';
      button.addEventListener('click', () => downloadArtifact(artifact));
      row.append(text, button);
      root.appendChild(row);
    });
  }

  async function downloadArtifact(artifact) {
    try {
      const response = await fetch(API + '/artifacts/' + encodeURIComponent(artifact.artifact_id), {
        headers: identityHeaders(requestId('download'))
      });
      if (!response.ok) throw new Error(await parseError(response));
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = artifact.filename || artifact.title || 'artifact';
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { setStatus(error.message, 'error'); }
  }

  function initialize() {
    $('tenant-id').value = localStorage.getItem('qwenpaw-office-tenant') || 'test-tenant';
    $('user-id').value = localStorage.getItem('qwenpaw-office-user') || 'test-user';
    fetch('/config.json').then((response) => response.json()).then((config) => {
      $('api-target').value = config.apiUrl;
    }).catch(() => { $('api-target').value = '配置读取失败'; });
    $('check-health').addEventListener('click', checkHealth);
    $('create-session').addEventListener('click', createSession);
    $('close-session').addEventListener('click', closeSession);
    $('upload-files').addEventListener('click', uploadFiles);
    $('message-form').addEventListener('submit', sendMessage);
    $('cancel-turn').addEventListener('click', cancelTurn);
    $('refresh-artifacts').addEventListener('click', refreshArtifacts);
    $('clear-events').addEventListener('click', () => { $('events').innerHTML = '<li class="muted">等待执行</li>'; });
    $('tenant-id').addEventListener('change', saveIdentity);
    $('user-id').addEventListener('change', saveIdentity);
  }

  initialize();
}());
