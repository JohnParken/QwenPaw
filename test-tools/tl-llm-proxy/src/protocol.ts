import { ProxyError } from './errors.js';
import type { Message } from './providers/types.js';
export interface PromptVariable { name: string; value: string }
export interface TLMetadata { appId?: string; trCode?: string; trVersion?: string; timestamp?: number; requestId?: string }
export interface InitRequest extends TLMetadata { data: { prompt_variables?: PromptVariable[] } }
export interface ChatRequest extends TLMetadata { data: { session_id: string; txt: string; stream?: boolean; files?: { file_id?: string; url?: string; content_type?: string }[] } }
export interface TLSuccess<T> { code: 0; message: 'success'; data: T }
export const success = <T>(data: T): TLSuccess<T> => ({ code: 0, message: 'success', data });
export const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const forbidden = ['tools', 'tool_choice', 'functions', 'function_call', 'parallel_tool_calls', 'tool_calls', 'response_format', 'model'];
export function envelope(input: unknown): Record<string, unknown> {
  if (!object(input) || !object(input.data)) throw new ProxyError(400, 'Expected a JSON object with data');
  for (const layer of [input, input.data]) for (const key of forbidden) if (Object.hasOwn(layer, key)) throw new ProxyError(400, `Unsupported field: ${key}`);
  for (const key of ['appId', 'trCode', 'trVersion', 'requestId']) if (Object.hasOwn(input, key) && typeof input[key] !== 'string') throw new ProxyError(400, `Invalid metadata: ${key}`);
  if (Object.hasOwn(input, 'timestamp') && (typeof input.timestamp !== 'number' || !Number.isFinite(input.timestamp))) throw new ProxyError(400, 'Invalid metadata: timestamp');
  return input.data;
}
export function promptVariables(data: Record<string, unknown>, systemName: string): readonly PromptVariable[] {
  const variables = data.prompt_variables === undefined ? [] : data.prompt_variables;
  if (!Array.isArray(variables)) throw new ProxyError(400, 'prompt_variables must be an array');
  const names = new Set<string>();
  const result = variables.map(item => {
    if (!object(item) || typeof item.name !== 'string' || !item.name.trim() || names.has(item.name)) throw new ProxyError(400, 'Invalid or duplicate prompt variable');
    const legacyNameOnly = item.name === 'name' && !Object.hasOwn(item, 'value');
    if (!legacyNameOnly && typeof item.value !== 'string') throw new ProxyError(400, 'Invalid or duplicate prompt variable');
    names.add(item.name);
    return Object.freeze({ name: item.name, value: legacyNameOnly ? '' : item.value as string });
  });
  if (result.some(item => item.name !== 'name') && !result.find(item => item.name === systemName)?.value.trim()) throw new ProxyError(400, 'A nonblank configured system prompt variable is required');
  return Object.freeze(result);
}
export function chatData(data: Record<string, unknown>): { sessionId: string; txt: string; stream: boolean } {
  if (typeof data.session_id !== 'string' || !data.session_id.trim()) throw new ProxyError(400, 'Invalid session_id');
  if (typeof data.txt !== 'string') throw new ProxyError(400, 'txt must be a string');
  if (Object.hasOwn(data, 'stream') && typeof data.stream !== 'boolean') throw new ProxyError(400, 'stream must be boolean');
  if (data.files !== undefined) {
    if (!Array.isArray(data.files) || data.files.some(file => !object(file) || Object.entries(file).some(([key, value]) => !['file_id', 'url', 'content_type'].includes(key) || value !== ''))) throw new ProxyError(400, 'Real attachments are unsupported');
  }
  return { sessionId: data.session_id, txt: data.txt, stream: data.stream !== false };
}
export function messagesFor(variables: readonly PromptVariable[], txt: string, systemName: string, legacy: boolean): Message[] {
  const system = variables.find(variable => variable.name === systemName);
  if (system) return [{ role: 'system', content: system.value }, { role: 'user', content: txt }];
  if (!legacy) return [{ role: 'user', content: txt }];
  const markers = [...txt.matchAll(/^(system|user|assistant):/gm)];
  if (!markers.length) return [{ role: 'user', content: txt }];
  const messages: Message[] = [];
  const prefix = txt.slice(0, markers[0]!.index).trim();
  if (prefix) messages.push({ role: 'user', content: prefix });
  markers.forEach((match, index) => messages.push({ role: match[1] as Message['role'], content: txt.slice(match.index! + match[0].length, markers[index + 1]?.index ?? txt.length).trim() }));
  return messages;
}
