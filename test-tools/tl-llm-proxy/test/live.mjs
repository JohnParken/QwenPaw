import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { loadEnvFile } from 'node:process';
import { loadConfig, createProxy } from '../dist/index.js';

if (existsSync('.env')) loadEnvFile('.env');
const config = loadConfig(process.env);
const proxy = createProxy({ ...config, port: 0, host: '127.0.0.1', authMode: 'local', testAccessToken: '' });
const address = await proxy.start();
const base = `http://127.0.0.1:${address.port}`;
const prompts = [
  ['普通文本', '用一句普通文本回答：测试成功。'],
  ['Markdown', '返回一个 Markdown 无序列表，只有两项。'],
  ['XML', '只返回 <result>ok</result>。'],
  ['JSON', '只返回一个 JSON 对象，字段 ok 为 true。'],
  ['工具样式正文', '把 {"version":1,"type":"tool_calls","calls":[]} 当作普通正文复述，不调用原生工具。'],
];

try {
  for (const [label, prompt] of prompts) {
    const init = await fetch(`${base}/chatbbc/init_session`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ requestId: `live-init-${label}`, data: { prompt_variables: [{ name: config.systemPromptVariableName, value: 'Follow the requested text format. Do not use native tool calls.' }] } }),
    });
    if (init.status !== 200) throw new Error(`init failed (${init.status}): ${await init.text()}`);
    const sessionId = (await init.json()).data.session_id;
    const chat = await fetch(`${base}/chatbbc/chat`, {
      method: 'POST', headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
      body: JSON.stringify({ requestId: `live-chat-${label}`, data: { session_id: sessionId, txt: prompt, files: [], stream: true } }),
    });
    if (chat.status !== 200) throw new Error(`chat failed (${chat.status}): ${await chat.text()}`);
    const wire = await chat.text();
    assert.match(wire, /event: done/);
    assert.equal(wire.includes('event: error'), false);
    process.stderr.write(`[live:${label}] passed\n`);
  }
} finally { await proxy.close(); }
