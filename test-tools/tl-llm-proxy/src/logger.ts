import type { Writable } from 'node:stream';
import { ProxyError } from './errors.js';
import type { ProxyConfig } from './config.js';
import type { Direction } from './providers/types.js';

const sensitive = /^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api[-_]?key|access[-_]?token|test_access_token|upstream_api_key|token|password|secret)$/i;
export interface LogContext { requestId: string; sessionId?: string }

export function createLogger(config: ProxyConfig, sink: Writable = process.stdout) {
  let tail = Promise.resolve();
  let pendingBytes = 0;
  let sequence = 0;
  let failure: Error | undefined;
  const onError = () => { failure = new ProxyError(500, 'Wire logging failed'); };
  sink.on('error', onError);
  const secrets = [config.apiKey, config.testAccessToken].filter(Boolean);
  function redact(value: unknown): unknown {
    if (typeof value === 'string') {
      let result = value;
      for (const secret of secrets) result = result.split(secret).join('[REDACTED]');
      return result.replace(/([?&](?:api_key|api-key|key|token|access_token|secret|password)=)[^&\s"']*/gi, '$1[REDACTED]')
        .replace(/("(?:api_key|api-key|token|access_token|password|secret)"\s*:\s*)"(?:\\.|[^"\\])*"/gi, '$1"[REDACTED]"');
    }
    if (Array.isArray(value)) return value.map(redact);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, sensitive.test(k) ? '[REDACTED]' : redact(v)]));
    return value;
  }
  async function write(context: LogContext, direction: Direction, phase: string, payload: unknown): Promise<void> {
    if (failure) throw failure;
    if (config.logLevel !== 'debug') return;
    const line = JSON.stringify(redact({ timestamp: new Date().toISOString(), ...context, provider: config.provider, model: config.model, direction, sequence: ++sequence, phase, payload })) + '\n';
    const bytes = Buffer.byteLength(line);
    if (pendingBytes + bytes > config.maxLogQueueBytes) throw new ProxyError(500, 'Wire log queue limit exceeded');
    pendingBytes += bytes;
    const task = tail.then(() => new Promise<void>((resolve, reject) => {
      if (failure || sink.destroyed || sink.writableEnded) { reject(new ProxyError(500, 'Wire logging failed')); return; }
      sink.write(line, error => { if (error) { onError(); reject(failure); } else resolve(); });
    }));
    tail = task.catch(() => { onError(); });
    try { await task; } finally { pendingBytes -= bytes; }
  }
  return {
    write,
    async close(): Promise<void> {
      let timer: NodeJS.Timeout | undefined;
      try {
        await Promise.race([tail, new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new ProxyError(500, 'Wire log shutdown timed out')), config.shutdownTimeoutMs); })]);
        if (failure) throw failure;
      } finally { clearTimeout(timer); sink.off('error', onError); }
    },
  };
}
