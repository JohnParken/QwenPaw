import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const packageRoot = fileURLToPath(new URL('..', import.meta.url));
const temporaryRoot = await mkdtemp(join(tmpdir(), 'tl-llm-proxy-package-'));
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
let child;
let upstream;

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd, encoding: 'utf8', env: { ...process.env, npm_config_cache: join(temporaryRoot, 'npm-cache') },
  });
  assert.equal(result.status, 0, `${command} ${args.join(' ')} failed:\n${result.stdout}\n${result.stderr}`);
  return result.stdout;
}

try {
  const packed = JSON.parse(run(npm, ['pack', '--json', '--pack-destination', temporaryRoot], packageRoot));
  assert.equal(packed.length, 1);
  const tarball = join(temporaryRoot, packed[0].filename);
  const consumer = join(temporaryRoot, 'consumer');
  await mkdir(consumer);
  run(npm, ['init', '--yes'], consumer);
  run(npm, ['install', '--ignore-scripts', '--no-audit', '--no-fund', tarball], consumer);

  let upstreamRequest;
  upstream = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    upstreamRequest = JSON.parse(Buffer.concat(chunks));
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ choices: [{ index: 0, message: { role: 'assistant', content: 'packed-cli-ok' }, finish_reason: 'stop' }] }));
  });
  upstream.listen(0, '127.0.0.1');
  await once(upstream, 'listening');
  const upstreamBase = `http://127.0.0.1:${upstream.address().port}`;
  const cli = join(consumer, 'node_modules', 'tl-llm-proxy-test', 'dist', 'cli.js');
  child = spawn(process.execPath, [cli], {
    cwd: consumer,
    env: {
      ...process.env, TL_PROXY_HOST: '127.0.0.1', TL_PROXY_PORT: '0', LOG_LEVEL: 'info',
      UPSTREAM_PROVIDER: 'openai-compatible', UPSTREAM_MODEL: 'package-smoke',
      UPSTREAM_BASE_URL: upstreamBase, UPSTREAM_API_KEY: 'synthetic-package-key',
    },
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  let stderr = '';
  const base = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`CLI startup timed out: ${stderr}`)), 2_000);
    child.stderr.on('data', chunk => {
      stderr += chunk;
      const match = stderr.match(/listening on (http:\/\/[^\s]+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
    child.once('exit', code => { clearTimeout(timer); reject(new Error(`CLI exited (${code}): ${stderr}`)); });
  });
  const init = await fetch(`${base}/chatbbc/init_session`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ data: { prompt_variables: [{ name: 'system_prompt', value: 'package system' }] } }),
  });
  assert.equal(init.status, 200);
  const sessionId = (await init.json()).data.session_id;
  const chat = await fetch(`${base}/chatbbc/chat`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ data: { session_id: sessionId, txt: 'package user', files: [], stream: false } }),
  });
  assert.equal(chat.status, 200);
  assert.equal((await chat.json()).data.txt, 'packed-cli-ok');
  assert.deepEqual(upstreamRequest.messages, [
    { role: 'system', content: 'package system' },
    { role: 'user', content: 'package user' },
  ]);
  process.stdout.write(`Package smoke passed: ${packed[0].filename}\n`);
} finally {
  if (child && child.exitCode === null) {
    child.kill('SIGTERM');
    await Promise.race([once(child, 'exit'), new Promise(resolve => setTimeout(resolve, 1_000))]);
    if (child.exitCode === null) child.kill('SIGKILL');
  }
  if (upstream) { upstream.close(); await once(upstream, 'close').catch(() => {}); }
  await rm(temporaryRoot, { recursive: true, force: true });
}
