import type { ProviderConfig } from './providers/types.js';

export interface ProxyConfig extends ProviderConfig {
  host: string; port: number; systemPromptVariableName: string; legacyRoleText: boolean;
  logLevel: 'debug' | 'info' | 'warn' | 'error';
  timeoutMs: number; bodyTimeoutMs: number; shutdownTimeoutMs: number;
  maxRequestBytes: number; maxDownstreamWireBytes: number; maxDownstreamEventBytes: number;
  sessionTtlMs: number; maxSessions: number; maxInflightRequests: number; maxLogQueueBytes: number;
  authMode: 'local' | 'bearer'; testAccessToken: string; corsAllowedOrigins: string[];
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): ProxyConfig {
  const required = (name: string): string => {
    const value = env[name];
    if (!value?.trim()) throw new Error(`${name} is required`);
    return value;
  };
  const number = (name: string, fallback: number, min = 1): number => {
    const value = env[name] === undefined ? fallback : Number(env[name]);
    if (!Number.isSafeInteger(value) || value < min || !String(env[name] ?? fallback).trim()) throw new Error(`Invalid ${name}`);
    return value;
  };
  const choice = <T extends string>(name: string, values: readonly T[], fallback: T): T => {
    const value = env[name] ?? fallback;
    if (!values.includes(value as T)) throw new Error(`Invalid ${name}`);
    return value as T;
  };
  const config: ProxyConfig = {
    host: env.TL_PROXY_HOST ?? '127.0.0.1', port: number('TL_PROXY_PORT', 8089, 0),
    provider: choice('UPSTREAM_PROVIDER', ['deepseek', 'qwen', 'openai-compatible'], required('UPSTREAM_PROVIDER') as ProviderConfig['provider']),
    model: required('UPSTREAM_MODEL'), baseUrl: required('UPSTREAM_BASE_URL'), apiKey: required('UPSTREAM_API_KEY'),
    thinking: choice('UPSTREAM_THINKING', ['provider-default', 'enabled', 'disabled'], 'provider-default'),
    ...(env.UPSTREAM_MAX_TOKENS !== undefined ? { maxTokens: number('UPSTREAM_MAX_TOKENS', 1) } : {}),
    systemPromptVariableName: env.SYSTEM_PROMPT_VARIABLE_NAME ?? 'system_prompt',
    legacyRoleText: choice('LEGACY_ROLE_TEXT', ['true', 'false'], 'true') === 'true',
    logLevel: choice('LOG_LEVEL', ['debug', 'info', 'warn', 'error'], 'debug'),
    timeoutMs: number('UPSTREAM_TIMEOUT_MS', 120_000), idleTimeoutMs: number('STREAM_IDLE_TIMEOUT_MS', 30_000),
    bodyTimeoutMs: number('REQUEST_BODY_TIMEOUT_MS', 30_000), shutdownTimeoutMs: number('SHUTDOWN_TIMEOUT_MS', 5_000),
    maxRequestBytes: number('MAX_REQUEST_BYTES', 1_048_576), maxResponseBytes: number('MAX_RESPONSE_BYTES', 4_194_304),
    maxUpstreamWireBytes: number('MAX_UPSTREAM_WIRE_BYTES', 67_108_864),
    maxDownstreamWireBytes: number('MAX_DOWNSTREAM_WIRE_BYTES', 67_108_864),
    maxSseEventBytes: number('MAX_SSE_EVENT_BYTES', 262_144), maxDownstreamEventBytes: number('MAX_DOWNSTREAM_EVENT_BYTES', 1_048_576),
    sessionTtlMs: number('SESSION_TTL_MS', 900_000), maxSessions: number('MAX_SESSIONS', 10_000),
    maxInflightRequests: number('MAX_INFLIGHT_REQUESTS', 32), maxLogQueueBytes: number('MAX_LOG_QUEUE_BYTES', 134_217_728),
    authMode: choice('AUTH_MODE', ['local', 'bearer'], 'local'), testAccessToken: env.TEST_ACCESS_TOKEN ?? '',
    corsAllowedOrigins: (env.CORS_ALLOWED_ORIGINS ?? '').split(',').map(s => s.trim()).filter(Boolean),
  };
  return validateConfig(config);
}

export function validateConfig(config: ProxyConfig): ProxyConfig {
  for (const key of ['host', 'model', 'baseUrl', 'apiKey', 'systemPromptVariableName', 'testAccessToken'] as const) {
    if (typeof config[key] !== 'string') throw new Error(`Invalid ${key}`);
  }
  if (!Array.isArray(config.corsAllowedOrigins) || config.corsAllowedOrigins.some(origin => typeof origin !== 'string')) throw new Error('Invalid corsAllowedOrigins');
  for (const [key, value] of Object.entries(config)) {
    if (typeof value === 'number' && (!Number.isSafeInteger(value) || value < (key === 'port' ? 0 : 1))) throw new Error(`Invalid ${key}`);
  }
  if (config.port > 65535) throw new Error('Invalid port');
  if (!['deepseek', 'qwen', 'openai-compatible'].includes(config.provider)) throw new Error('Invalid provider');
  if (!['provider-default', 'enabled', 'disabled'].includes(config.thinking)) throw new Error('Invalid thinking');
  if (!['debug', 'info', 'warn', 'error'].includes(config.logLevel)) throw new Error('Invalid logLevel');
  if (!['local', 'bearer'].includes(config.authMode)) throw new Error('Invalid authMode');
  if (typeof config.legacyRoleText !== 'boolean') throw new Error('Invalid legacyRoleText');
  if (!config.model.trim()) throw new Error('Model is required');
  if (!config.systemPromptVariableName.trim() || config.systemPromptVariableName !== config.systemPromptVariableName.trim() || config.systemPromptVariableName === 'name') throw new Error('Invalid system prompt variable name');
  if (config.authMode === 'local' && !['127.0.0.1', '::1', 'localhost'].includes(config.host)) throw new Error('Local mode requires a loopback host');
  if (config.authMode === 'bearer' && !config.testAccessToken.trim()) throw new Error('Bearer mode requires TEST_ACCESS_TOKEN');
  if (config.provider === 'openai-compatible' && config.thinking !== 'provider-default') throw new Error('Generic providers require provider-default thinking');
  let url: URL;
  try { url = new URL(config.baseUrl); } catch { throw new Error('Invalid upstream URL'); }
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('Upstream URL must be HTTP(S), without credentials, query or fragment');
  for (const origin of config.corsAllowedOrigins) {
    try { if (new URL(origin).origin !== origin) throw new Error(); } catch { throw new Error('CORS requires explicit origins'); }
  }
  return Object.freeze({ ...config, corsAllowedOrigins: Object.freeze([...config.corsAllowedOrigins]) as unknown as string[] });
}
