import { ProxyError } from '../errors.js';
import { SseParser, type SseRecord, utf8ByteLength } from './sse.js';
import type {
  Completion,
  Message,
  Provider,
  ProviderConfig,
  ProviderEvent,
  ProviderRequest,
  ProviderStream,
} from './types.js';

type ProviderProfile = 'deepseek' | 'qwen' | 'openai-compatible';

interface ProviderResponse {
  response: Response;
  signal: AbortSignal;
  dispose: () => Promise<void>;
}

interface ResponseText {
  text: string;
  bytes: number;
}

interface StreamValidation {
  content?: string;
  finish: boolean;
  controlOnly: boolean;
}

const JSON_CONTENT_TYPE = /(?:^|;)\s*application\/json\s*(?:;|$)/i;
const SSE_CONTENT_TYPE = /(?:^|;)\s*text\/event-stream\s*(?:;|$)/i;

/**
 * Build the one fixed upstream endpoint while retaining a configured path
 * prefix.  A base URL may itself already name /chat/completions.
 */
export function chatCompletionsUrl(baseUrl: string): string {
  let url: URL;
  try {
    url = new URL(baseUrl);
  } catch {
    throw new ProxyError(500, 'Invalid upstream base URL');
  }

  const path = url.pathname.replace(/\/+$/, '');
  if (!/(?:^|\/)chat\/completions$/.test(path)) {
    url.pathname = `${path || ''}/chat/completions`;
  } else {
    url.pathname = path || '/chat/completions';
  }
  return url.toString();
}

/** Build the common OpenAI-compatible body without any native tool fields. */
export function buildRequestBody(
  config: ProviderConfig,
  messages: Message[],
  stream: boolean,
  profile: ProviderProfile = config.provider,
): Record<string, unknown> {
  if (!Array.isArray(messages)) throw new ProxyError(400, 'Invalid provider messages');
  for (const message of messages) {
    if (
      message === null ||
      typeof message !== 'object' ||
      !(['system', 'user', 'assistant'] as string[]).includes(message.role) ||
      typeof message.content !== 'string'
    ) {
      throw new ProxyError(400, 'Invalid provider message');
    }
  }

  const body: Record<string, unknown> = {
    model: config.model,
    messages,
    stream,
    n: 1,
  };
  if (config.maxTokens !== undefined) body.max_tokens = config.maxTokens;

  if (profile === 'deepseek') {
    if (config.thinking === 'enabled') body.thinking = { type: 'enabled' };
    else if (config.thinking === 'disabled') body.thinking = { type: 'disabled' };
  } else if (profile === 'qwen') {
    if (config.thinking === 'enabled') body.enable_thinking = true;
    else if (config.thinking === 'disabled') body.enable_thinking = false;
  } else if (config.thinking !== 'provider-default') {
    throw new ProxyError(500, 'Thinking mode is unsupported for openai-compatible provider');
  }
  return body;
}

/** The public factory requested by the standalone proxy package. */
export function createProvider(config: ProviderConfig): Provider {
  switch (config.provider) {
    case 'deepseek':
      return createCompatibleProvider(config, 'deepseek');
    case 'qwen':
      return createCompatibleProvider(config, 'qwen');
    case 'openai-compatible':
      return createCompatibleProvider(config, 'openai-compatible');
    default:
      throw new ProxyError(500, 'Unsupported upstream provider');
  }
}

export default createProvider;

/** Internal factory used by the provider-specific adapter entry points. */
export function createCompatibleProvider(config: ProviderConfig, profile: ProviderProfile): Provider {
  return new OpenAICompatibleProvider(config, profile);
}

class OpenAICompatibleProvider implements Provider {
  public constructor(
    private readonly config: ProviderConfig,
    private readonly profile: ProviderProfile,
  ) {}

  public async complete(request: ProviderRequest): Promise<Completion> {
    const upstream = await this.open(request, false);
    try {
      const response = upstream.response;
      const contentType = response.headers.get('content-type') ?? '';
      if (!JSON_CONTENT_TYPE.test(contentType)) {
        const invalid = await readResponseText(response, this.config.maxUpstreamWireBytes);
        await logWire(request, 'upstream_to_proxy', 'body', invalid.text);
        throw new ProxyError(502, 'Upstream returned a non-JSON response');
      }

      const result = await readResponseText(response, this.config.maxUpstreamWireBytes);
      await logWire(request, 'upstream_to_proxy', 'body', result.text);
      return parseCompletion(result.text, this.config.maxResponseBytes);
    } catch (error) {
      throw normalizeUpstreamError(error, request.signal);
    } finally {
      await upstream.dispose();
    }
  }

  public async stream(request: ProviderRequest): Promise<ProviderStream> {
    const upstream = await this.open(request, true);
    const contentType = upstream.response.headers.get('content-type') ?? '';
    if (!SSE_CONTENT_TYPE.test(contentType)) {
      try {
        const invalid = await readResponseText(upstream.response, this.config.maxUpstreamWireBytes);
        await logWire(request, 'upstream_to_proxy', 'body', invalid.text);
      } finally {
        await upstream.dispose();
      }
      throw new ProxyError(502, 'Upstream returned a non-SSE response');
    }
    const body = upstream.response.body;
    if (body === null) {
      await upstream.dispose();
      throw new ProxyError(502, 'Upstream response has no body');
    }

    const reader = body.getReader();
    let cancelled = false;
    let disposed = false;
    let disposePromise: Promise<void> | undefined;
    const dispose = async (): Promise<void> => {
      if (disposePromise !== undefined) return disposePromise;
      disposePromise = (async () => {
        if (disposed) return;
        disposed = true;
        try {
          await reader.cancel();
        } catch {
          // Reader cancellation is best effort after a network failure.
        } finally {
          reader.releaseLock();
          await upstream.dispose();
        }
      })();
      return disposePromise;
    };

    const cancel = async (): Promise<void> => {
      cancelled = true;
      await dispose();
    };

    const events = this.readStream(request, reader, upstream.signal, () => cancelled, dispose);
    return { events, cancel };
  }

  private async open(request: ProviderRequest, stream: boolean): Promise<ProviderResponse> {
    const requestBody = JSON.stringify(buildRequestBody(this.config, request.messages, stream, this.profile));
    if (requestBody === undefined) throw new ProxyError(500, 'Unable to encode upstream request');

    const controller = new AbortController();
    let requestAborted = false;
    const abortFromRequest = (): void => {
      requestAborted = true;
      controller.abort(request.signal.reason);
    };
    if (request.signal.aborted) abortFromRequest();
    else request.signal.addEventListener('abort', abortFromRequest, { once: true });

    const headers: Record<string, string> = {
      Accept: stream ? 'text/event-stream' : 'application/json',
      'Content-Type': 'application/json',
    };
    if (this.config.apiKey.length > 0) headers.Authorization = `Bearer ${this.config.apiKey}`;
    const url = chatCompletionsUrl(this.config.baseUrl);

    const dispose = async (): Promise<void> => {
      request.signal.removeEventListener('abort', abortFromRequest);
      controller.abort();
    };

    try {
      await logWire(request, 'proxy_to_upstream', 'request', { method: 'POST', url });
      await logWire(request, 'proxy_to_upstream', 'headers', headers);
      await logWire(request, 'proxy_to_upstream', 'body', requestBody);
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: requestBody,
        signal: controller.signal,
      });
      await logWire(request, 'upstream_to_proxy', 'headers', {
        status: response.status,
        headers: Object.fromEntries(response.headers.entries()),
      });

      if (!response.ok) {
        let bodyText = '';
        try {
          const result = await readResponseText(response, this.config.maxUpstreamWireBytes);
          bodyText = result.text;
          await logWire(request, 'upstream_to_proxy', 'body', bodyText);
        } catch (error) {
          // Preserve the configured wire limit mapping while retaining the
          // original HTTP status for ordinary provider failures.
          await logWire(request, 'upstream_to_proxy', 'error', safeMessage(error));
        }
        throw new ProxyError(mapHttpStatus(response.status), upstreamHttpMessage(response.status));
      }
      return { response, signal: controller.signal, dispose };
    } catch (error) {
      await dispose();
      if (error instanceof ProxyError) throw error;
      if (requestAborted || request.signal.aborted || controller.signal.aborted) {
        throw new ProxyError(504, 'Upstream request timed out');
      }
      throw normalizeUpstreamError(error, request.signal, 'connection');
    }
  }

  private async *readStream(
    request: ProviderRequest,
    reader: ReadableStreamDefaultReader<Uint8Array>,
    signal: AbortSignal,
    wasCancelled: () => boolean,
    dispose: () => Promise<void>,
  ): AsyncIterable<ProviderEvent> {
    const parser = new SseParser(this.config.maxSseEventBytes);
    const decoder = new TextDecoder('utf-8', { fatal: true });
    let wireBytes = 0;
    const state = { contentBytes: 0, sawFinish: false, sawDone: false, yieldedDone: false };

    try {
      while (true) {
        let read: ReadableStreamReadResult<Uint8Array>;
        try {
          read = await readWithIdleTimeout(reader, this.config.idleTimeoutMs, signal);
        } catch (error) {
          if (wasCancelled() || signal.aborted) return;
          throw normalizeUpstreamError(error, request.signal, 'stream');
        }

        if (read.done) {
          let tail: string;
          try {
            tail = decoder.decode();
          } catch {
            throw new ProxyError(502, 'Upstream returned invalid UTF-8');
          }
          for (const record of parser.feed(tail)) {
            yield* this.processRecord(request, record, state);
          }
          for (const record of parser.finish()) {
            yield* this.processRecord(request, record, state);
          }
          break;
        }

        wireBytes += read.value.byteLength;
        if (wireBytes > this.config.maxUpstreamWireBytes) {
          throw new ProxyError(502, 'Upstream response exceeds the configured wire limit');
        }

        let text: string;
        try {
          text = decoder.decode(read.value, { stream: true });
        } catch {
          throw new ProxyError(502, 'Upstream returned invalid UTF-8');
        }
        for (const record of parser.feed(text)) {
          yield* this.processRecord(request, record, state);
        }
      }

      if (!state.sawFinish || !state.sawDone || state.yieldedDone) {
        if (!state.sawFinish) throw new ProxyError(502, 'Upstream stream ended without finish_reason: stop');
        if (!state.sawDone) throw new ProxyError(502, 'Upstream stream ended before [DONE]');
        throw new ProxyError(502, 'Upstream stream terminated more than once');
      }
      state.yieldedDone = true;
      yield { type: 'done' };
    } catch (error) {
      if (wasCancelled() || signal.aborted) return;
      const normalized = normalizeUpstreamError(error, request.signal, 'stream');
      await logWire(request, 'upstream_to_proxy', 'error', normalized.message);
      throw normalized;
    } finally {
      await dispose();
    }
  }

  private async *processRecord(
    request: ProviderRequest,
    record: SseRecord,
    state: {
      contentBytes: number;
      sawFinish: boolean;
      sawDone: boolean;
      yieldedDone: boolean;
    },
  ): AsyncIterable<ProviderEvent> {
    await logWire(request, 'upstream_to_proxy', 'event', record.raw);
    if (record.data === null) return;
    if (record.data === '[DONE]') {
      if (!state.sawFinish || state.sawDone || state.yieldedDone) {
        throw new ProxyError(502, 'Unexpected upstream [DONE] marker');
      }
      state.sawDone = true;
      return;
    }
    if (state.sawDone) throw new ProxyError(502, 'Upstream sent data after [DONE]');

    let payload: unknown;
    try {
      payload = JSON.parse(record.data);
    } catch {
      throw new ProxyError(502, 'Upstream SSE data is not valid JSON');
    }
    const result = validateStreamPayload(payload);
    if (state.sawFinish && !result.controlOnly) {
      throw new ProxyError(502, 'Upstream sent data after finish_reason');
    }
    if (result.finish) {
      if (state.sawFinish) throw new ProxyError(502, 'Upstream sent duplicate finish_reason');
      state.sawFinish = true;
    }
    if (result.content !== undefined) {
      if (state.sawFinish && result.finish === false) {
        throw new ProxyError(502, 'Upstream sent content after finish_reason');
      }
      state.contentBytes += utf8ByteLength(result.content);
      if (state.contentBytes > this.config.maxResponseBytes) {
        throw new ProxyError(502, 'Upstream content exceeds the configured response limit');
      }
      // Empty strings are valid content but need no downstream frame.
      if (result.content.length > 0) yield { type: 'content', content: result.content };
    }
  }
}

function validateStreamPayload(payload: unknown): StreamValidation {
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new ProxyError(502, 'Invalid upstream stream envelope');
  }
  const object = payload as Record<string, unknown>;
  if (object.error !== undefined && object.error !== null) {
    throw new ProxyError(502, 'Upstream returned an error event');
  }

  const choices = object.choices;
  if (choices === undefined) {
    if (object.usage !== undefined) return { finish: false, controlOnly: true };
    throw new ProxyError(502, 'Invalid upstream stream choices');
  }
  if (!Array.isArray(choices)) throw new ProxyError(502, 'Invalid upstream stream choices');
  if (choices.length === 0) {
    if (object.usage !== undefined) return { finish: false, controlOnly: true };
    throw new ProxyError(502, 'Invalid upstream stream choices');
  }
  if (choices.length !== 1) throw new ProxyError(502, 'Upstream returned multiple choices');
  const choice = choices[0];
  if (choice === null || typeof choice !== 'object' || Array.isArray(choice)) {
    throw new ProxyError(502, 'Invalid upstream stream choice');
  }
    const choiceObject = choice as Record<string, unknown>;
  if (choiceObject.index !== undefined && choiceObject.index !== 0) {
    throw new ProxyError(502, 'Upstream returned a non-zero choice index');
  }
  validateFinishReason(choiceObject.finish_reason);
  validateNativeFields(choiceObject);
  validateRefusal(choiceObject.refusal);

  let content: string | undefined;
  const delta = choiceObject.delta;
  if (delta !== undefined) {
    if (delta === null || typeof delta !== 'object' || Array.isArray(delta)) {
      throw new ProxyError(502, 'Invalid upstream delta');
    }
    const deltaObject = delta as Record<string, unknown>;
    validateNativeFields(deltaObject);
    validateRefusal(deltaObject.refusal);
    validateOptionalString(deltaObject.reasoning_content);
    validateOptionalString(deltaObject.reasoning);
    if (deltaObject.content !== undefined && deltaObject.content !== null) {
      if (typeof deltaObject.content !== 'string') {
        throw new ProxyError(502, 'Upstream delta content must be a string');
      }
      content = deltaObject.content;
    }
  } else if (choiceObject.message !== undefined) {
    // A message in a streaming response is tolerated as a provider quirk,
    // but its content still follows the same strict validation.
    if (choiceObject.message === null || typeof choiceObject.message !== 'object') {
      throw new ProxyError(502, 'Invalid upstream message');
    }
    const message = choiceObject.message as Record<string, unknown>;
    validateNativeFields(message);
    validateRefusal(message.refusal);
    if (message.content !== undefined && message.content !== null) {
      if (typeof message.content !== 'string') throw new ProxyError(502, 'Upstream message content must be a string');
      content = message.content;
    }
  }

  return { content, finish: choiceObject.finish_reason === 'stop', controlOnly: false };
}

function parseCompletion(text: string, maxResponseBytes: number): Completion {
  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new ProxyError(502, 'Upstream response is not valid JSON');
  }
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new ProxyError(502, 'Invalid upstream completion envelope');
  }
  const object = payload as Record<string, unknown>;
  if (object.error !== undefined && object.error !== null) {
    throw new ProxyError(502, 'Upstream returned an error response');
  }
  if (!Array.isArray(object.choices) || object.choices.length !== 1) {
    throw new ProxyError(502, 'Invalid upstream completion choices');
  }
  const choice = object.choices[0];
  if (choice === null || typeof choice !== 'object' || Array.isArray(choice)) {
    throw new ProxyError(502, 'Invalid upstream completion choice');
  }
  const choiceObject = choice as Record<string, unknown>;
  if (choiceObject.index !== undefined && choiceObject.index !== 0) {
    throw new ProxyError(502, 'Upstream returned a non-zero choice index');
  }
  if (choiceObject.finish_reason !== 'stop') {
    throw new ProxyError(502, 'Upstream completion did not finish with stop');
  }
  validateNativeFields(choiceObject);
  validateRefusal(choiceObject.refusal);
  const message = choiceObject.message;
  if (message === null || typeof message !== 'object' || Array.isArray(message)) {
    throw new ProxyError(502, 'Invalid upstream completion message');
  }
  const messageObject = message as Record<string, unknown>;
  validateNativeFields(messageObject);
  validateRefusal(messageObject.refusal);
  if (typeof messageObject.content !== 'string') {
    throw new ProxyError(502, 'Upstream completion content must be a string');
  }
  if (utf8ByteLength(messageObject.content) > maxResponseBytes) {
    throw new ProxyError(502, 'Upstream content exceeds the configured response limit');
  }
  const completion: Completion = {
    content: messageObject.content,
    finishReason: 'stop',
  };
  if (object.usage !== undefined) completion.usage = object.usage;
  return completion;
}

function validateFinishReason(value: unknown): void {
  if (value === undefined || value === null) return;
  if (value !== 'stop') throw new ProxyError(502, 'Unsupported upstream finish reason');
}

function validateNativeFields(object: Record<string, unknown>): void {
  for (const key of ['tool_calls', 'function_call']) {
    const value = object[key];
    if (value === undefined || value === null) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    throw new ProxyError(502, 'Upstream native tool calls are unsupported');
  }
}

function validateRefusal(value: unknown): void {
  if (value !== undefined && value !== null && value !== '') {
    throw new ProxyError(502, 'Upstream refusal fields are unsupported');
  }
}

function validateOptionalString(value: unknown): void {
  if (value !== undefined && value !== null && typeof value !== 'string') {
    throw new ProxyError(502, 'Upstream control content must be a string');
  }
}

async function readResponseText(response: Response, maxWireBytes: number): Promise<ResponseText> {
  if (response.body === null) throw new ProxyError(502, 'Upstream response has no body');
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let bytes = 0;
  let text = '';
  try {
    while (true) {
      const read = await reader.read();
      if (read.done) {
        try {
          text += decoder.decode();
        } catch {
          throw new ProxyError(502, 'Upstream returned invalid UTF-8');
        }
        return { text, bytes };
      }
      bytes += read.value.byteLength;
      if (bytes > maxWireBytes) throw new ProxyError(502, 'Upstream response exceeds the configured wire limit');
      try {
        text += decoder.decode(read.value, { stream: true });
      } catch {
        throw new ProxyError(502, 'Upstream returned invalid UTF-8');
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // Best effort; the fetch body is already complete or failed.
    }
    reader.releaseLock();
  }
}

async function readWithIdleTimeout(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  idleTimeoutMs: number,
  signal: AbortSignal,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  const readPromise = reader.read();
  let timer: NodeJS.Timeout | undefined;
  let removeAbortListener: (() => void) | undefined;
  try {
    const abortPromise = new Promise<never>((_, reject) => {
      const abort = () => reject(signal.reason ?? new DOMException('The operation was aborted', 'AbortError'));
      removeAbortListener = () => signal.removeEventListener('abort', abort);
      if (signal.aborted) abort();
      else signal.addEventListener('abort', abort, { once: true });
    });
    return await Promise.race([
      readPromise,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new ProxyError(504, 'Upstream stream idle timeout')), idleTimeoutMs);
      }),
      abortPromise,
    ]);
  } finally {
    clearTimeout(timer);
    removeAbortListener?.();
  }
}

async function logWire(request: ProviderRequest, direction: 'proxy_to_upstream' | 'upstream_to_proxy', phase: string, payload: unknown): Promise<void> {
  try {
    await request.log(direction, phase, payload);
  } catch (error) {
    if (error instanceof ProxyError) throw error;
    throw new ProxyError(500, 'Wire logging failed');
  }
}

const diagnosticCodes = new Set([
  'ECONNRESET', 'ECONNREFUSED', 'ECONNABORTED', 'ETIMEDOUT', 'EPIPE', 'ENOTFOUND', 'EAI_AGAIN',
  'UND_ERR_SOCKET', 'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT',
]);
const diagnosticSummaries: Record<string, string> = {
  ECONNRESET: 'connection reset', ECONNREFUSED: 'connection refused', ECONNABORTED: 'connection aborted',
  ETIMEDOUT: 'connection timed out', EPIPE: 'broken pipe', ENOTFOUND: 'host not found', EAI_AGAIN: 'DNS lookup delayed',
  UND_ERR_SOCKET: 'socket failure', UND_ERR_CONNECT_TIMEOUT: 'connect timed out',
  UND_ERR_HEADERS_TIMEOUT: 'headers timed out', UND_ERR_BODY_TIMEOUT: 'body timed out',
};
const diagnosticTypes = new Set(['Error', 'TypeError', 'DOMException', 'SocketError', 'ConnectTimeoutError', 'HeadersTimeoutError', 'BodyTimeoutError']);

export function normalizeUpstreamError(error: unknown, signal: AbortSignal, phase: 'connection' | 'stream' = 'connection'): ProxyError {
  if (error instanceof ProxyError) return error;
  if (signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
    return new ProxyError(504, 'Upstream request timed out');
  }
  const code = upstreamErrorCode(error);
  const type = upstreamErrorType(error);
  const kind = phase === 'stream' ? 'stream' : 'connection';
  const detail = code ? `${code}: ${diagnosticSummaries[code]}` : undefined;
  const suffix = [`type=${type}`, detail].filter(Boolean).join(', ');
  return new ProxyError(502, `Upstream ${kind} failed${suffix ? ` (${suffix})` : ''}`);
}

function upstreamErrorType(error: unknown): string {
  let current: unknown = error;
  for (let depth = 0; depth < 5 && current && typeof current === 'object'; depth++) {
    let name: unknown;
    try { name = (current as { name?: unknown }).name; } catch { name = undefined; }
    if (typeof name === 'string' && diagnosticTypes.has(name)) return name;
    try { current = (current as { cause?: unknown }).cause; } catch { current = undefined; }
  }
  return 'Error';
}

function upstreamErrorCode(error: unknown): string | undefined {
  let current: unknown = error;
  for (let depth = 0; depth < 5 && current && typeof current === 'object'; depth++) {
    let code: unknown;
    try { code = (current as { code?: unknown }).code; } catch { code = undefined; }
    if (typeof code === 'string' && diagnosticCodes.has(code.toUpperCase())) return code.toUpperCase();
    try { current = (current as { cause?: unknown }).cause; } catch { current = undefined; }
  }
  return undefined;
}

function mapHttpStatus(status: number): number {
  if (status === 408 || status === 504) return 504;
  if (status === 429 || status === 503) return 503;
  return 502;
}

function upstreamHttpMessage(status: number): string {
  if (status === 408 || status === 504) return 'Upstream request timed out';
  if (status === 429 || status === 503) return 'Upstream provider is temporarily unavailable';
  return 'Upstream provider returned an error';
}

function safeMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Upstream response could not be read';
}
