import { once } from 'node:events';
import { createServer } from 'node:http';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createProvider, normalizeUpstreamError } from '../dist/providers/openai-compatible.js';

const message = { role: 'user', content: 'hello' };

test('normalizes nested upstream socket failures without leaking unsafe details', () => {
  const error = new Error('fetch failed', { cause: Object.assign(new Error('socket reset'), { code: 'UND_ERR_SOCKET' }) });
  const normalized = normalizeUpstreamError(error, new AbortController().signal, 'connection');
  assert.equal(normalized.status, 502);
  assert.match(normalized.message, /Upstream connection failed \(type=Error, UND_ERR_SOCKET: socket failure\)/);

  const unsafe = normalizeUpstreamError(new Error('https://user:pass@example.test/chat?api_key=secret prompt=do-not-leak'), new AbortController().signal, 'stream');
  assert.equal(unsafe.message, 'Upstream stream failed (type=Error)');
  assert.equal(normalizeUpstreamError(new Error('sk-123 user personal text'), new AbortController().signal).message, 'Upstream connection failed (type=Error)');
  assert.match(normalizeUpstreamError(Object.assign(new TypeError('secret'), { cause: Object.assign(new Error('x'), { code: 'ECONNRESET' }) }), new AbortController().signal).message, /type=TypeError/);
});

function config(overrides = {}) {
  return {
    provider: 'openai-compatible',
    model: 'test-model',
    baseUrl: 'http://127.0.0.1/',
    apiKey: 'synthetic-key',
    thinking: 'provider-default',
    idleTimeoutMs: 500,
    maxResponseBytes: 1024 * 1024,
    maxUpstreamWireBytes: 1024 * 1024,
    maxSseEventBytes: 64 * 1024,
    ...overrides,
  };
}

async function withUpstream(handler, callback) {
  const server = createServer(handler);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = server.address();
  try {
    return await callback(`http://127.0.0.1:${address.port}`);
  } finally {
    server.close();
    await once(server, 'close');
  }
}

function request(log = async () => {}) {
  return { messages: [message], signal: new AbortController().signal, log };
}

test('uses a path-prefixed chat completions URL and preserves an opaque completion', async () => {
  await withUpstream(async (req, res) => {
    assert.equal(req.url, '/gateway/chat/completions');
    assert.equal(req.headers.authorization, 'Bearer synthetic-key');
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    assert.equal(body.model, 'test-model');
    assert.equal(body.stream, false);
    assert.equal(body.n, 1);
    assert.equal(body.messages[0].content, message.content);
    assert.equal('tools' in body, false);
    assert.equal('response_format' in body, false);
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ choices: [{ index: 0, message: { role: 'assistant', content: '{"a": 1}' }, finish_reason: 'stop' }] }));
  }, async (baseUrl) => {
    const provider = createProvider(config({ baseUrl: `${baseUrl}/gateway/` }));
    const result = await provider.complete(request());
    assert.deepEqual(result, { content: '{"a": 1}', finishReason: 'stop' });
  });
});

test('maps thinking settings only in their provider adapter', async () => {
  await withUpstream(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    assert.deepEqual(body.thinking, { type: 'enabled' });
    assert.equal('enable_thinking' in body, false);
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ choices: [{ message: { content: '' }, finish_reason: 'stop' }] }));
  }, async (baseUrl) => {
    const provider = createProvider(config({ provider: 'deepseek', thinking: 'enabled', baseUrl }));
    assert.equal((await provider.complete(request())).content, '');
  });

  await withUpstream(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    assert.equal(body.enable_thinking, false);
    assert.equal('thinking' in body, false);
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ choices: [{ message: { content: '' }, finish_reason: 'stop' }] }));
  }, async (baseUrl) => {
    const provider = createProvider(config({ provider: 'qwen', thinking: 'disabled', baseUrl }));
    assert.equal((await provider.complete(request())).content, '');
  });
});

test('decodes bounded incremental SSE, including BOM, CRLF, comments and multiline data', async () => {
  await withUpstream(async (_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream; charset=utf-8' });
    res.write(Buffer.from([0xef, 0xbb, 0xbf]));
    res.write(': heartbeat\r\n\r\ndata: {"choices":[\r\ndata: {"index":0,"delta":{"content":"你"}}\r\ndata: ]}\r\n\r\n');
    await new Promise(resolve => setTimeout(resolve, 30));
    res.write('data: {"choices":[{"index":0,"delta":{"content":"好"},"finish_reason":"stop"}]}\r\n\r\n');
    res.write('data: {"choices":[],"usage":{"completion_tokens":2}}\r\n\r\n');
    res.end('data: [DONE]\r\n\r\n');
  }, async (baseUrl) => {
    const provider = createProvider(config({ baseUrl }));
    const stream = await provider.stream(request());
    const iterator = stream.events[Symbol.asyncIterator]();
    const first = await iterator.next();
    assert.deepEqual(first.value, { type: 'content', content: '你' });
    const events = [first.value];
    for await (const event of { [Symbol.asyncIterator]: () => iterator }) events.push(event);
    assert.deepEqual(events, [
      { type: 'content', content: '你' },
      { type: 'content', content: '好' },
      { type: 'done' },
    ]);
  });
});

test('rejects non-stop completion and native tool responses', async () => {
  await withUpstream(async (_req, res) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ choices: [{ message: { content: 'partial' }, finish_reason: 'length' }] }));
  }, async (baseUrl) => {
    const provider = createProvider(config({ baseUrl }));
    await assert.rejects(provider.complete(request()), error => error?.status === 502);
  });

  await withUpstream(async (_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.end('data: {"choices":[{"delta":{"tool_calls":[{"id":"x"}]}}]}\n\n');
  }, async (baseUrl) => {
    const provider = createProvider(config({ baseUrl }));
    const stream = await provider.stream(request());
    await assert.rejects((async () => { for await (const _event of stream.events) {} })(), error => error?.status === 502);
  });
});

test('accepts CR-only SSE delimiters including a final CR at EOF', async () => {
  await withUpstream(async (_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.end('data: {"choices":[{"index":0,"delta":{"content":"cr"},"finish_reason":"stop"}]}\r\rdata: [DONE]\r\r');
  }, async (baseUrl) => {
    const provider = createProvider(config({ baseUrl }));
    const stream = await provider.stream(request());
    const events = [];
    for await (const event of stream.events) events.push(event);
    assert.deepEqual(events, [{ type: 'content', content: 'cr' }, { type: 'done' }]);
  });
});

test('fails an idle upstream stream and cancels its reader', async () => {
  await withUpstream(async (_req, res) => {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.flushHeaders();
    await once(res, 'close');
  }, async (baseUrl) => {
    const provider = createProvider(config({ baseUrl, idleTimeoutMs: 30 }));
    const stream = await provider.stream(request());
    await assert.rejects((async () => { for await (const _event of stream.events) {} })(), error => error?.status === 504);
  });
});
