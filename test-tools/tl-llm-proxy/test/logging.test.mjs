import test from 'node:test';
import assert from 'node:assert/strict';
import { Writable } from 'node:stream';
import { createLogger } from '../dist/logger.js';
import { createProxy } from '../dist/server.js';
import { loadConfig } from '../dist/config.js';

const baseEnv = { UPSTREAM_PROVIDER: 'qwen', UPSTREAM_MODEL: 'test-model', UPSTREAM_BASE_URL: 'http://upstream.test/v1', UPSTREAM_API_KEY: 'api-secret', TL_PROXY_PORT: '0' };
const config = (overrides = {}) => loadConfig({ ...baseEnv, ...overrides });
function sink(options = {}) {
  const chunks = [];
  const output = new Writable({ write(chunk, _encoding, callback) { chunks.push(chunk.toString()); options.fail ? callback(new Error('sink failed')) : callback(); } });
  return { output, text: () => chunks.join('') };
}
const context = { requestId: 'req-1' };

test('debug logger redacts configured secrets and sensitive keys while retaining ordinary markers', async () => {
  const captured = sink();
  const logger = createLogger(config({ TEST_ACCESS_TOKEN: 'token-secret', AUTH_MODE: 'bearer' }), captured.output);
  await logger.write(context, 'client_to_proxy', 'request', {
    authorization: 'Bearer token-secret', cookie: 'sid=abc', apiKey: 'api-secret',
    body: 'ordinary-body-marker api-secret token-secret', marker: 'keep-me',
  });
  await logger.close();
  const text = captured.text();
  assert.match(text, /keep-me/);
  assert.match(text, /ordinary-body-marker/);
  assert.doesNotMatch(text, /api-secret|token-secret|sid=abc|Bearer token-secret/);
  assert.match(text, /\[REDACTED\]/);
});

test('info logger suppresses wire bodies', async () => {
  const captured = sink();
  const logger = createLogger(config({ LOG_LEVEL: 'info' }), captured.output);
  await logger.write(context, 'client_to_proxy', 'body', { marker: 'hidden-body' });
  await logger.close();
  assert.equal(captured.text(), '');
});

test('sink failure rejects explicitly without hanging', async () => {
  const captured = sink({ fail: true });
  const logger = createLogger(config(), captured.output);
  await assert.rejects(Promise.race([
    logger.write(context, 'client_to_proxy', 'body', { marker: 'x' }),
    new Promise((_, reject) => setTimeout(() => reject(new Error('hung')), 500)),
  ]), /Wire logging failed|sink failed/);
  await logger.close().catch(() => {});
});

test('full init and chat flow logs all four wire directions at debug level', async () => {
  const captured = sink();
  const calls = [];
  const provider = {
    async complete(request) {
      calls.push(request.messages);
      await request.log('proxy_to_upstream', 'body', { marker: 'upstream-request' });
      await request.log('upstream_to_proxy', 'body', { marker: 'upstream-response' });
      return { content: 'reply-marker', finishReason: 'stop' };
    },
    async stream() { throw new Error('stream should not be used'); },
  };
  const proxy = createProxy(config(), { provider, logSink: captured.output });
  const address = await proxy.start();
  try {
    const init = await fetch(`http://127.0.0.1:${address.port}/chatbbc/init_session`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ data: { prompt_variables: [{ name: 'system_prompt', value: 'system-marker' }] } }),
    });
    const session = (await init.json()).data.session_id;
    const chat = await fetch(`http://127.0.0.1:${address.port}/chatbbc/chat`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ data: { session_id: session, txt: 'body-marker', stream: false } }),
    });
    assert.equal(chat.status, 200);
    assert.match(await chat.text(), /reply-marker/);
  } finally { await proxy.close(); }
  assert.equal(calls.length, 1);
  const lines = captured.text().trim().split('\n').map(line => JSON.parse(line));
  assert.deepEqual(new Set(lines.map(line => line.direction)), new Set(['client_to_proxy', 'proxy_to_client', 'proxy_to_upstream', 'upstream_to_proxy']));
  assert.match(captured.text(), /body-marker/);
});
