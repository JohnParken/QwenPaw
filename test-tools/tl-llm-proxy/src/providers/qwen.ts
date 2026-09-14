import type { Provider, ProviderConfig } from './types.js';
import { createCompatibleProvider } from './openai-compatible.js';

/** Create the OpenAI-compatible transport with Qwen's thinking mapping. */
export function createQwenProvider(config: ProviderConfig): Provider {
  return createCompatibleProvider(config, 'qwen');
}

export default createQwenProvider;
