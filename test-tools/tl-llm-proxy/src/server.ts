import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { randomUUID, timingSafeEqual } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import type { Writable } from 'node:stream';
import { validateConfig, type ProxyConfig } from './config.js';
import { ProxyError, safeError } from './errors.js';
import { createLogger, type LogContext } from './logger.js';
import { envelope, promptVariables, chatData, messagesFor, success } from './protocol.js';
import { SessionStore } from './session-store.js';
import { createProvider } from './providers/openai-compatible.js';
import type { Provider, ProviderStream, WireLog } from './providers/types.js';

function abortable<T>(task: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason);
    if (signal.aborted) { task.catch(() => {}); abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    task.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

async function readBody(req: IncomingMessage, config: ProxyConfig, signal: AbortSignal, log: WireLog): Promise<unknown> {
  let chunks: Buffer[] = [];
  let size = 0;
  try {
    const raw = await new Promise<Buffer>((resolve, reject) => {
      const finish = (error?: Error) => {
        clearTimeout(timer);
        req.off('data', data); req.off('end', end); req.off('error', fail);
        signal.removeEventListener('abort', abort);
        if (error) { req.pause(); reject(error); } else resolve(Buffer.concat(chunks));
      };
      const data = (chunk: Buffer) => {
        size += chunk.length;
        if (size > config.maxRequestBytes) { finish(new ProxyError(413, 'Request body limit exceeded')); return; }
        chunks.push(chunk);
      };
      const end = () => finish();
      const fail = () => finish(new ProxyError(400, 'Request body read failed'));
      const abort = () => finish(signal.reason);
      const timer = setTimeout(() => finish(new ProxyError(408, 'Request body timeout')), config.bodyTimeoutMs);
      req.on('data', data); req.once('end', end); req.once('error', fail);
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) abort();
    });
    let body: unknown;
    try { body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw)); }
    catch { await log('client_to_proxy', 'invalid_body', { body: raw.toString('utf8') }); throw new ProxyError(400, 'Invalid JSON body'); }
    return body;
  } catch (error) {
    await log('client_to_proxy', 'body_failure', { bytesReceived: size, capturedBody: Buffer.concat(chunks).toString('utf8'), error: safeError(error).message });
    throw error;
  } finally { chunks = []; }
}

export interface ProxyOptions { provider?: Provider; logSink?: Writable }
export function createProxy(input: ProxyConfig, options: ProxyOptions = {}) {
  const config = validateConfig(input);
  const provider = options.provider ?? createProvider(config);
  const store = new SessionStore(config.sessionTtlMs, config.maxSessions);
  const controllers = new Set<AbortController>();
  const requests = new Set<Promise<void>>();
  let logger: ReturnType<typeof createLogger> | undefined;
  let closing = false;
  let startPromise: Promise<AddressInfo> | undefined;
  let closePromise: Promise<void> | undefined;

  async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const controller = new AbortController();
    const signal = controller.signal;
    const context: LogContext = { requestId: randomUUID() };
    const log: WireLog = (direction, phase, payload) => abortable(logger!.write(context, direction, phase, payload), signal);
    const disconnect = () => { if (!res.writableFinished) controller.abort(new ProxyError(499, 'Client disconnected')); };
    res.once('close', disconnect);
    req.once('aborted', disconnect);
    let upstream: ProviderStream | undefined;
    let timer: NodeJS.Timeout | undefined;
    let wireBytes = 0;
    let frameSequence = 0;
    let admitted = false;
    let failed = false;
    res.setHeader('X-Request-Id', context.requestId);

    async function write(chunk: string): Promise<void> {
      if (res.destroyed || res.writableEnded) throw new ProxyError(499, 'Client disconnected');
      await abortable(new Promise<void>((resolve, reject) => {
        res.write(chunk, error => error ? reject(new ProxyError(499, 'Client disconnected')) : resolve());
      }), signal);
    }
    async function json(status: number, body: unknown): Promise<void> {
      const raw = JSON.stringify(body);
      if (Buffer.byteLength(raw) > config.maxDownstreamWireBytes) throw new ProxyError(502, 'Downstream wire limit exceeded');
      res.statusCode = status;
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      await log('proxy_to_client', 'response', { status, headers: res.getHeaders(), body });
      await write(raw);
      res.end();
    }
    async function frame(event: string, data: unknown): Promise<void> {
      const raw = `id: ${frameSequence++}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
      const bytes = Buffer.byteLength(raw);
      if (bytes > config.maxDownstreamEventBytes || wireBytes + bytes > config.maxDownstreamWireBytes) throw new ProxyError(502, 'Downstream stream limit exceeded');
      await log('proxy_to_client', 'event', { event, raw });
      await write(raw);
      wireBytes += bytes;
    }
    try {
      if (closing || controllers.size >= config.maxInflightRequests) throw new ProxyError(503, 'Request capacity reached');
      admitted = true;
      controllers.add(controller);
      timer = setTimeout(() => controller.abort(new ProxyError(504, 'Proxy deadline exceeded')), config.timeoutMs);
      const path = new URL(req.url ?? '/', 'http://local.invalid').pathname;
      await log('client_to_proxy', 'headers', { method: req.method, path: req.url, headers: req.headers });
      if (!['/chatbbc/init_session', '/chatbbc/chat'].includes(path)) throw new ProxyError(404, 'Route not found');
      const origin = req.headers.origin;
      if (origin && config.corsAllowedOrigins.includes(origin)) {
        res.setHeader('Access-Control-Allow-Origin', origin); res.setHeader('Vary', 'Origin');
        res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
        res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
      }
      if (req.method === 'OPTIONS') {
        if (origin && !config.corsAllowedOrigins.includes(origin)) throw new ProxyError(403, 'Origin not allowed');
        res.setHeader('Allow', 'POST, OPTIONS');
        res.statusCode = 204;
        await log('proxy_to_client', 'response', { status: 204, headers: res.getHeaders() });
        res.end(); return;
      }
      if (req.method !== 'POST') { res.setHeader('Allow', 'POST, OPTIONS'); throw new ProxyError(405, 'Method not allowed'); }
      const principal = config.authMode === 'local' ? 'local' : 'test';
      if (config.authMode === 'bearer') {
        const expected = Buffer.from(`Bearer ${config.testAccessToken}`);
        const supplied = Buffer.from(req.headers.authorization ?? '');
        if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
          res.setHeader('WWW-Authenticate', 'Bearer'); throw new ProxyError(401, 'Invalid test credential');
        }
      }
      if (!/^application\/json(?:\s*;|$)/i.test(req.headers['content-type'] ?? '')) throw new ProxyError(400, 'Content-Type must be application/json');
      const body = await readBody(req, config, signal, log);
      await log('client_to_proxy', 'request', { method: req.method, path: req.url, headers: req.headers, body });
      const data = envelope(body);
      if (path === '/chatbbc/init_session') {
        const session = store.create(promptVariables(data, config.systemPromptVariableName), principal, `${config.provider}:${config.model}`);
        context.sessionId = session.id;
        await json(200, success({ session_id: session.id })); return;
      }
      const chat = chatData(data);
      context.sessionId = chat.sessionId;
      const session = store.get(chat.sessionId, principal);
      const request = { messages: messagesFor(session.variables, chat.txt, config.systemPromptVariableName, config.legacyRoleText), signal, log };
      if (!chat.stream) {
        const result = await provider.complete(request);
        if (Buffer.byteLength(result.content) > config.maxResponseBytes) throw new ProxyError(502, 'Model content limit exceeded');
        await json(200, success({ txt: result.content })); return;
      }
      upstream = await provider.stream(request);
      res.statusCode = 200;
      res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
      res.setHeader('Cache-Control', 'no-cache, no-transform');
      res.setHeader('X-Accel-Buffering', 'no');
      await log('proxy_to_client', 'headers', { status: 200, headers: res.getHeaders() });
      res.flushHeaders();
      let done = false;
      let contentBytes = 0;
      for await (const event of upstream.events) {
        if (done) throw new ProxyError(502, 'Upstream event after completion');
        if (event.type === 'done') { done = true; continue; }
        contentBytes += Buffer.byteLength(event.content);
        if (contentBytes > config.maxResponseBytes) throw new ProxyError(502, 'Model content limit exceeded');
        if (event.content) await frame('chunk', { content: event.content });
      }
      if (!done) throw new ProxyError(502, 'Upstream stream ended prematurely');
      await frame('done', { finished: true });
      res.end();
    } catch (error) {
      failed = true;
      const normalized = safeError(signal.aborted ? signal.reason : error);
      // Error output has its own small bounded path: the original deadline or log sink may have failed.
      if (!res.destroyed && !res.writableEnded) {
        if (!req.complete) res.setHeader('Connection', 'close');
        const isStream = res.headersSent;
        const payload = isStream ? { message: normalized.message } : { error: normalized.message };
        const raw = isStream ? `event: error\ndata: ${JSON.stringify(payload)}\n\n` : JSON.stringify(payload);
        let logTimer: NodeJS.Timeout | undefined;
        try {
          await Promise.race([logger!.write(context, 'proxy_to_client', 'error', { status: isStream ? 200 : normalized.status, body: payload }), new Promise<void>(resolve => { logTimer = setTimeout(resolve, 100); })]);
        } catch { /* The protocol error itself makes failed logging visible to the caller. */ }
        finally { clearTimeout(logTimer); }
        if (!isStream) { res.statusCode = normalized.status; res.setHeader('Content-Type', 'application/json; charset=utf-8'); }
        res.end(raw);
      }
    } finally {
      clearTimeout(timer);
      controller.abort(new ProxyError(499, 'Request finished'));
      await upstream?.cancel().catch(() => {});
      if (admitted) controllers.delete(controller);
      req.off('aborted', disconnect);
      // Keep close detection until Node flushes the final frame; on failure close incomplete request bodies.
      if (failed && !req.complete) res.once('finish', () => req.destroy());
    }
  }
  const server = createServer((req, res) => {
    const task: Promise<void> = handle(req, res).catch(() => { res.destroy(); }).finally(() => { requests.delete(task); });
    requests.add(task);
  });
  server.requestTimeout = config.bodyTimeoutMs;
  server.headersTimeout = Math.min(config.bodyTimeoutMs, 60_000);
  return {
    get address(): AddressInfo | null { const value = server.address(); return value && typeof value !== 'string' ? value : null; },
    start(): Promise<AddressInfo> {
      if (closing) return Promise.reject(new Error('Proxy is closed'));
      if (startPromise) return startPromise;
      logger = createLogger(config, options.logSink);
      startPromise = new Promise((resolve, reject) => {
        const error = (cause: Error) => { server.off('listening', listening); reject(cause); };
        const listening = () => { server.off('error', error); resolve(server.address() as AddressInfo); };
        server.once('error', error); server.once('listening', listening);
        server.listen(config.port, config.host);
      });
      return startPromise;
    },
    close(): Promise<void> {
      if (closePromise) return closePromise;
      closing = true;
      closePromise = (async () => {
        await startPromise?.catch(() => {});
        let timer: NodeJS.Timeout | undefined;
        try {
          const stopped = new Promise<void>(resolve => server.close(() => resolve()));
          timer = setTimeout(() => {
            for (const controller of controllers) controller.abort(new ProxyError(503, 'Proxy shutting down'));
            server.closeAllConnections();
          }, config.shutdownTimeoutMs);
          await stopped;
          await Promise.allSettled([...requests]);
        } finally { clearTimeout(timer); store.clear(); await logger?.close(); }
      })();
      return closePromise;
    },
  };
}
