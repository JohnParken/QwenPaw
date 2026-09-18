import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:http';
import { test } from 'node:test';
import { createProxy } from '../dist/index.js';

function config(baseUrl, overrides = {}) {
  return {
    host: '127.0.0.1', port: 0, provider: 'openai-compatible', model: 'fake-model', baseUrl,
    apiKey: 'synthetic-upstream-key', thinking: 'provider-default', systemPromptVariableName: 'system_prompt',
    legacyRoleText: true, logLevel: 'info', timeoutMs: 2_000, idleTimeoutMs: 500,
    bodyTimeoutMs: 500, shutdownTimeoutMs: 500, maxRequestBytes: 1_048_576,
    maxResponseBytes: 1_048_576, maxUpstreamWireBytes: 1_048_576,
    maxDownstreamWireBytes: 1_048_576, maxSseEventBytes: 64 * 1024,
    maxDownstreamEventBytes: 1_048_576, sessionTtlMs: 5_000, maxSessions: 10,
    maxInflightRequests: 4, maxLogQueueBytes: 1_048_576, authMode: 'local',
    testAccessToken: '', corsAllowedOrigins: [], ...overrides,
  };
}

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return `http://127.0.0.1:${server.address().port}`;
}

async function close(server) {
  server.close();
  await once(server, 'close');
}

async function init(base, system = 'opaque system') {
  const response = await fetch(`${base}/chatbbc/init_session`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ requestId: 'init-stream-test', data: { prompt_variables: [{ name: 'system_prompt', value: system }] } }),
  });
  assert.equal(response.status, 200);
  return (await response.json()).data.session_id;
}

async function readUntil(reader, expected) {
  const decoder = new TextDecoder();
  let value = '';
  while (!value.includes(expected)) {
    const part = await reader.read();
    assert.equal(part.done, false, `stream ended before ${expected}`);
    value += decoder.decode(part.value, { stream: true });
  }
  return value;
}

test('forwards the first upstream delta before upstream completion', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let capturedBody;
  const upstream = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    capturedBody = JSON.parse(Buffer.concat(chunks));
    res.writeHead(200, { 'content-type': 'text/event-stream; charset=utf-8' });
    res.write('data: {"choices":[{"index":0,"delta":{"content":"首段 🚀"}}]}\n\n');
    await gate;
    res.write('data: {"choices":[{"index":0,"delta":{"content":" 尾段"},"finish_reason":"stop"}]}\n\n');
    res.end('data: [DONE]\n\n');
  });
  const upstreamBase = await listen(upstream);
  const proxy = createProxy(config(upstreamBase));
  const address = await proxy.start();
  const base = `http://127.0.0.1:${address.port}`;
  try {
    const sessionId = await init(base, 'system:\n保持原样');
    const response = await fetch(`${base}/chatbbc/chat`, {
      method: 'POST', headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
      body: JSON.stringify({ requestId: 'chat-stream-test', data: { session_id: sessionId, txt: 'user:\n正文 tool_calls', files: [], stream: true } }),
    });
    assert.equal(response.status, 200);
    assert.match(response.headers.get('cache-control'), /no-transform/);
    const reader = response.body.getReader();
    const first = await Promise.race([
      readUntil(reader, 'event: chunk'),
      new Promise((_, reject) => setTimeout(() => reject(new Error('first TL chunk was buffered')), 400)),
    ]);
    assert.match(first, /"content":"首段 🚀"/);
    assert.deepEqual(capturedBody.messages, [
      { role: 'system', content: 'system:\n保持原样' },
      { role: 'user', content: 'user:\n正文 tool_calls' },
    ]);
    assert.equal(capturedBody.stream, true);
    assert.equal('tools' in capturedBody, false);
    release();
    let remainder = '';
    const decoder = new TextDecoder();
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      remainder += decoder.decode(part.value, { stream: true });
    }
    assert.match(first + remainder, /"content":" 尾段"/);
    assert.equal((first + remainder).match(/event: done/g)?.length, 1);
    assert.equal((first + remainder).includes('event: error'), false);
  } finally {
    release();
    await proxy.close();
    await close(upstream);
  }
});

test('turns premature upstream EOF into one TL error without done', async () => {
  const upstream = createServer((_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.end('data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n');
  });
  const upstreamBase = await listen(upstream);
  const proxy = createProxy(config(upstreamBase));
  const address = await proxy.start();
  const base = `http://127.0.0.1:${address.port}`;
  try {
    const sessionId = await init(base);
    const response = await fetch(`${base}/chatbbc/chat`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ data: { session_id: sessionId, txt: '{}', stream: true } }),
    });
    assert.equal(response.status, 200);
    const text = await response.text();
    assert.match(text, /event: chunk/);
    assert.equal((text.match(/event: error/g) ?? []).length, 1);
    assert.match(text, /Upstream stream ended without finish_reason: stop/);
    assert.equal(text.includes('event: done'), false);
  } finally { await proxy.close(); await close(upstream); }
});

for (const mode of ['reset', 'invalid-json']) {
  test(`preserves ${mode} diagnostics in downstream SSE`, async () => {
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    const upstream = createServer(async (_req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' });
      res.write('data: {"choices":[{"delta":{"content":"partial"}}]}\n\n');
      await gate;
      if (mode === 'reset') res.destroy();
      else res.end('data: {invalid-json}\n\n');
    });
    const upstreamBase = await listen(upstream);
    const proxy = createProxy(config(upstreamBase));
    const address = await proxy.start();
    const base = `http://127.0.0.1:${address.port}`;
    try {
      const sessionId = await init(base);
      const response = await fetch(`${base}/chatbbc/chat`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ data: { session_id: sessionId, txt: '{}', stream: true } }),
      });
      const reader = response.body.getReader();
      let text = await readUntil(reader, 'event: chunk');
      release();
      const decoder = new TextDecoder();
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        text += decoder.decode(part.value, { stream: true });
      }
      assert.equal((text.match(/event: error/g) ?? []).length, 1);
      assert.equal(text.includes('event: done'), false);
      if (mode === 'reset') {
        assert.match(text, /Upstream stream failed \(type=TypeError, UND_ERR_SOCKET: socket failure\)/);
      } else {
        assert.match(text, /Upstream SSE data is not valid JSON/);
      }
    } finally { release(); await proxy.close(); await close(upstream); }
  });
}

test('rejects a non-SSE streaming response before sending success headers', async () => {
  const upstream = createServer((_req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ choices: [{ message: { content: 'buffered' }, finish_reason: 'stop' }] }));
  });
  const upstreamBase = await listen(upstream);
  const proxy = createProxy(config(upstreamBase));
  const address = await proxy.start();
  const base = `http://127.0.0.1:${address.port}`;
  try {
    const sessionId = await init(base);
    const response = await fetch(`${base}/chatbbc/chat`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ data: { session_id: sessionId, txt: 'x', stream: true } }),
    });
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error: 'Upstream returned a non-SSE response' });
  } finally { await proxy.close(); await close(upstream); }
});
