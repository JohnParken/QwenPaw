import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createProxy } from '../dist/index.js';

const config = (overrides = {}) => ({ provider: 'openai-compatible', model: 'm', baseUrl: 'http://127.0.0.1:1', apiKey: 'k', thinking: 'provider-default', host: '127.0.0.1', port: 0, systemPromptVariableName: 'system_prompt', legacyRoleText: true, logLevel: 'error', timeoutMs: 2000, idleTimeoutMs: 500, bodyTimeoutMs: 1000, shutdownTimeoutMs: 200, maxRequestBytes: 1024 * 1024, maxResponseBytes: 1024 * 1024, maxUpstreamWireBytes: 1024 * 1024, maxDownstreamWireBytes: 1024 * 1024, maxSseEventBytes: 64 * 1024, maxDownstreamEventBytes: 64 * 1024, sessionTtlMs: 60_000, maxSessions: 1, maxInflightRequests: 1, maxLogQueueBytes: 1024 * 1024, authMode: 'local', testAccessToken: '', corsAllowedOrigins: [], ...overrides });

const provider = { async complete() { return { content: 'ok', finishReason: 'stop' }; }, async stream() { return { events: (async function* () { yield { type: 'content', content: 'ok' }; yield { type: 'done' }; })(), cancel: async () => {} }; } };
const post = (base, body) => fetch(`${base}/chatbbc/init_session`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });

test('session capacity is bounded and start/close are idempotent', async () => {
  const proxy = createProxy(config(), { provider });
  const address = await proxy.start();
  assert.equal((await post(`http://127.0.0.1:${address.port}`, { data: {} })).status, 200);
  assert.equal((await post(`http://127.0.0.1:${address.port}`, { data: {} })).status, 503);
  await Promise.all([proxy.close(), proxy.close()]);
  await assert.rejects(proxy.start(), /closed/);
});
