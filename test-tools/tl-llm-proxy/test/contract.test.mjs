import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createProxy } from '../dist/index.js';

const baseConfig = (overrides = {}) => ({
  provider: 'openai-compatible', model: 'fake-model', baseUrl: 'http://127.0.0.1:1', apiKey: 'fake-key',
  thinking: 'provider-default', host: '127.0.0.1', port: 0, systemPromptVariableName: 'system_prompt',
  legacyRoleText: true, logLevel: 'error', timeoutMs: 2000, idleTimeoutMs: 500, bodyTimeoutMs: 1000,
  shutdownTimeoutMs: 500, maxRequestBytes: 1024 * 1024, maxResponseBytes: 1024 * 1024,
  maxUpstreamWireBytes: 1024 * 1024, maxDownstreamWireBytes: 1024 * 1024, maxSseEventBytes: 64 * 1024,
  maxDownstreamEventBytes: 64 * 1024, sessionTtlMs: 60_000, maxSessions: 10, maxInflightRequests: 8,
  maxLogQueueBytes: 1024 * 1024, authMode: 'local', testAccessToken: '', corsAllowedOrigins: [], ...overrides,
});

function fakeProvider(content = '答复') {
  const calls = [];
  return {
    calls,
    async complete(request) { calls.push(request); return { content, finishReason: 'stop' }; },
    async stream(request) {
      calls.push(request);
      return { events: (async function* () { yield { type: 'content', content: '你' }; yield { type: 'content', content: content.slice(1) }; yield { type: 'done' }; })(), cancel: async () => {} };
    },
  };
}

async function running(config, provider, fn) {
  const proxy = createProxy(config, { provider });
  const address = await proxy.start();
  try { return await fn(`http://127.0.0.1:${address.port}`); } finally { await Promise.all([proxy.close(), proxy.close()]); }
}

async function post(base, path, body, headers = {}) {
  const response = await fetch(`${base}${path}`, { method: 'POST', headers: { 'content-type': 'application/json', ...headers }, body: JSON.stringify(body) });
  return { response, body: await response.json() };
}

test('init is native, and non-stream chat preserves strings and messages', async () => {
  const provider = fakeProvider('{"未闭合":');
  await running(baseConfig(), provider, async base => {
    const init = await post(base, '/chatbbc/init_session', { data: { prompt_variables: [{ name: 'system_prompt', value: '系统\n原样' }, { name: 'tenant', value: 'x' }] } });
    assert.equal(init.response.status, 200);
    assert.equal(init.body.code, 0);
    const chat = await post(base, '/chatbbc/chat', { data: { session_id: init.body.data.session_id, txt: 'system: 保持\n{"x":', stream: false } });
    assert.equal(chat.response.status, 200);
    assert.equal(chat.body.data.txt, '{"未闭合":');
    assert.deepEqual(provider.calls[0].messages, [{ role: 'system', content: '系统\n原样' }, { role: 'user', content: 'system: 保持\n{"x":' }]);
  });
});

test('stream forwards chunks and exactly one done event', async () => {
  const provider = fakeProvider('你好');
  await running(baseConfig(), provider, async base => {
    const init = await post(base, '/chatbbc/init_session', { data: { prompt_variables: [{ name: 'system_prompt', value: 's' }] } });
    const response = await fetch(`${base}/chatbbc/chat`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ data: { session_id: init.body.data.session_id, txt: '正文' } }) });
    const text = await response.text();
    assert.equal(response.status, 200);
    assert.deepEqual([...text.matchAll(/event: chunk\ndata: (.+)\n/g)].map(match => JSON.parse(match[1]).content).join(''), '你好');
    assert.equal((text.match(/event: done/g) ?? []).length, 1);
    assert.match(text, /"finished":true/);
  });
});

test('rejects native control fields, real attachments, and unknown sessions', async () => {
  const provider = fakeProvider();
  await running(baseConfig(), provider, async base => {
    const forbidden = await post(base, '/chatbbc/init_session', { data: { system_prompt: 's', tools: [] } });
    assert.equal(forbidden.response.status, 400);
    const init = await post(base, '/chatbbc/init_session', { data: { prompt_variables: [{ name: 'system_prompt', value: 's' }] } });
    const attachment = await post(base, '/chatbbc/chat', { data: { session_id: init.body.data.session_id, txt: 'x', files: [{ url: 'https://example.test/a' }] } });
    assert.equal(attachment.response.status, 400);
    const unknown = await post(base, '/chatbbc/chat', { data: { session_id: 'missing', txt: 'x' } });
    assert.equal(unknown.response.status, 404);
    assert.equal(provider.calls.length, 0);
  });
});

test('bearer auth rejects missing credentials and accepts the configured token', async () => {
  const provider = fakeProvider();
  await running(baseConfig({ authMode: 'bearer', testAccessToken: 'test-token' }), provider, async base => {
    assert.equal((await post(base, '/chatbbc/init_session', { data: {} })).response.status, 401);
    const ok = await post(base, '/chatbbc/init_session', { data: {} }, { authorization: 'Bearer test-token' });
    assert.equal(ok.response.status, 200);
  });
});

test('legacy name-only init is accepted without changing the fixed route', async () => {
  const provider = fakeProvider('legacy');
  await running(baseConfig(), provider, async base => {
    const init = await post(base, '/chatbbc/init_session?source=legacy', { data: { prompt_variables: [{ name: 'name' }] } });
    assert.equal(init.response.status, 200);
    const chat = await post(base, '/chatbbc/chat', { data: { session_id: init.body.data.session_id, txt: 'system: legacy system\nuser: legacy user', stream: false } });
    assert.equal(chat.response.status, 200);
    assert.deepEqual(provider.calls[0].messages, [
      { role: 'system', content: 'legacy system' },
      { role: 'user', content: 'legacy user' },
    ]);
  });
});

test('rejects invalid metadata and stream types before provider access', async () => {
  const provider = fakeProvider();
  await running(baseConfig(), provider, async base => {
    const metadata = await post(base, '/chatbbc/init_session', { timestamp: 'now', data: {} });
    assert.equal(metadata.response.status, 400);
    const init = await post(base, '/chatbbc/init_session', { data: {} });
    const invalidStream = await post(base, '/chatbbc/chat', { data: { session_id: init.body.data.session_id, txt: 'x', stream: 'false' } });
    assert.equal(invalidStream.response.status, 400);
    assert.equal(provider.calls.length, 0);
  });
});
