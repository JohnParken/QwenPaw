export class ProxyError extends Error {
  constructor(public readonly status: number, message: string) { super(message); this.name = 'ProxyError'; }
}
export function safeError(error: unknown): ProxyError {
  return error instanceof ProxyError ? error : new ProxyError(500, 'Internal proxy failure');
}
