import type { Provider, ProviderConfig } from './types.js';
import { createCompatibleProvider } from './openai-compatible.js';

/** Create the OpenAI-compatible transport with DeepSeek's thinking mapping. */
export function createDeepSeekProvider(config: ProviderConfig): Provider {
  return createCompatibleProvider(config, 'deepseek');
}

export default createDeepSeekProvider;
