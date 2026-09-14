#!/usr/bin/env node
import { loadEnvFile } from 'node:process';
import { loadConfig } from './config.js';
import { createProxy } from './server.js';

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  if (args.includes('--help')) {
    process.stdout.write('Test-only TL proxy. Usage: tl-llm-proxy [--env-file PATH]\nSee .env.example for required upstream configuration.\n'); return;
  }
  if (args.length) {
    if (args.length !== 2 || args[0] !== '--env-file' || !args[1]) throw new Error('Usage: tl-llm-proxy [--env-file PATH]');
    loadEnvFile(args[1]);
  }
  const proxy = createProxy(loadConfig());
  try {
    const address = await proxy.start();
    process.stderr.write(`Test-only TL proxy listening on http://${address.family === 'IPv6' ? `[${address.address}]` : address.address}:${address.port}\n`);
  } catch (error) { await proxy.close(); throw error; }
  let stopping = false;
  const stop = () => {
    if (stopping) return;
    stopping = true;
    proxy.close().catch(() => { process.stderr.write('Proxy shutdown failed\n'); process.exitCode = 1; }).finally(() => {
      process.off('SIGINT', stop); process.off('SIGTERM', stop);
    });
  };
  process.on('SIGINT', stop); process.on('SIGTERM', stop);
}
main().catch(() => { process.stderr.write('Proxy startup failed. Check required configuration, listen address and log sink.\n'); process.exitCode = 1; });
