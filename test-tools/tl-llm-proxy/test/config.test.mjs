import test from 'node:test';
import assert from 'node:assert/strict';
import { loadConfig } from '../dist/config.js';

const base = {
  UPSTREAM_PROVIDER: 'qwen',
  UPSTREAM_MODEL: 'test-model',
  UPSTREAM_BASE_URL: 'http://upstream.test/v1',
  UPSTREAM_API_KEY: 'api-secret',
};
const env = (overrides = {}) => ({ ...base, ...overrides });

test('loadConfig applies documented defaults', () => {
  const config = loadConfig(env());
  assert.equal(config.host, '127.0.0.1');
  assert.equal(config.port, 8089);
  assert.equal(config.thinking, 'provider-default');
  assert.equal(config.systemPromptVariableName, 'system_prompt');
  assert.equal(config.legacyRoleText, true);
  assert.equal(config.logLevel, 'debug');
  assert.equal(config.authMode, 'local');
  assert.deepEqual(config.corsAllowedOrigins, []);
  assert(Object.isFrozen(config));
});

test('loadConfig accepts provider thinking, host, bearer, and CORS settings', () => {
  const config = loadConfig(env({
    UPSTREAM_PROVIDER: 'deepseek', UPSTREAM_THINKING: 'enabled',
    TL_PROXY_HOST: 'localhost', TL_PROXY_PORT: '0', AUTH_MODE: 'bearer',
    TEST_ACCESS_TOKEN: 'test-token', CORS_ALLOWED_ORIGINS: 'https://one.test, https://two.test',
    UPSTREAM_BASE_URL: 'https://api.test/gateway', UPSTREAM_MAX_TOKENS: '128',
  }));
  assert.equal(config.thinking, 'enabled');
  assert.equal(config.port, 0);
  assert.equal(config.authMode, 'bearer');
  assert.equal(config.testAccessToken, 'test-token');
  assert.deepEqual(config.corsAllowedOrigins, ['https://one.test', 'https://two.test']);
  assert.equal(config.maxTokens, 128);
});

test('loadConfig rejects missing required values and invalid choices', () => {
  assert.throws(() => loadConfig({ ...base, UPSTREAM_API_KEY: '' }), /UPSTREAM_API_KEY is required/);
  assert.throws(() => loadConfig(env({ UPSTREAM_PROVIDER: 'bogus' })), /Invalid UPSTREAM_PROVIDER/);
  assert.throws(() => loadConfig(env({ UPSTREAM_THINKING: 'bogus' })), /Invalid UPSTREAM_THINKING/);
  assert.throws(() => loadConfig(env({ LOG_LEVEL: 'trace' })), /Invalid LOG_LEVEL/);
  assert.throws(() => loadConfig(env({ AUTH_MODE: 'bearer' })), /Bearer mode requires TEST_ACCESS_TOKEN/);
});

test('loadConfig enforces local loopback, generic thinking, URL, and CORS invariants', () => {
  assert.throws(() => loadConfig(env({ TL_PROXY_HOST: '0.0.0.0' })), /Local mode requires/);
  assert.throws(() => loadConfig(env({ UPSTREAM_PROVIDER: 'openai-compatible', UPSTREAM_THINKING: 'enabled' })), /Generic providers/);
  assert.throws(() => loadConfig(env({ UPSTREAM_BASE_URL: 'https://u:p@api.test/v1' })), /HTTP\(S\)/);
  assert.throws(() => loadConfig(env({ UPSTREAM_BASE_URL: 'http://api.test/v1?x=1' })), /HTTP\(S\)/);
  assert.throws(() => loadConfig(env({ CORS_ALLOWED_ORIGINS: 'https://site.test/path' })), /CORS requires explicit origins/);
});

test('createProxy config validation rejects malformed JavaScript values', async () => {
  const { createProxy } = await import('../dist/index.js');
  const valid = loadConfig(env());
  assert.throws(() => createProxy({ ...valid, apiKey: 123 }), /Invalid apiKey/);
  assert.throws(() => createProxy({ ...valid, corsAllowedOrigins: 'https://site.test' }), /Invalid corsAllowedOrigins/);
});
