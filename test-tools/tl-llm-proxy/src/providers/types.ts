export interface Message { role: 'system' | 'user' | 'assistant'; content: string }
export type ProviderName = 'deepseek' | 'qwen' | 'openai-compatible';
export interface ProviderConfig {
  provider: ProviderName;
  model: string;
  baseUrl: string;
  apiKey: string;
  thinking: 'provider-default' | 'enabled' | 'disabled';
  maxTokens?: number;
  idleTimeoutMs: number;
  maxResponseBytes: number;
  maxUpstreamWireBytes: number;
  maxSseEventBytes: number;
}
export type Direction = 'client_to_proxy' | 'proxy_to_upstream' | 'upstream_to_proxy' | 'proxy_to_client';
export type WireLog = (direction: Direction, phase: string, payload: unknown) => Promise<void>;
export interface ProviderRequest { messages: Message[]; signal: AbortSignal; log: WireLog }
export interface Completion { content: string; finishReason: 'stop'; usage?: unknown }
export type ProviderEvent = { type: 'content'; content: string } | { type: 'done' };
export interface ProviderStream { events: AsyncIterable<ProviderEvent>; cancel(): Promise<void> }
export interface Provider {
  complete(request: ProviderRequest): Promise<Completion>;
  stream(request: ProviderRequest): Promise<ProviderStream>;
}
