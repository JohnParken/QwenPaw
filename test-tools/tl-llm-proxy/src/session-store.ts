import { randomUUID } from 'node:crypto';
import { ProxyError } from './errors.js';
import type { PromptVariable } from './protocol.js';
export interface Session { id: string; principal: string; routeId: string; variables: readonly PromptVariable[]; createdAt: number; expiresAt: number }
export class SessionStore {
  private readonly sessions = new Map<string, Session>();
  constructor(private readonly ttlMs: number, private readonly capacity: number, private readonly now = Date.now) {}
  create(variables: readonly PromptVariable[], principal: string, routeId: string): Session {
    const now = this.now();
    for (const [id, session] of this.sessions) if (session.expiresAt <= now) this.sessions.delete(id);
    if (this.sessions.size >= this.capacity) throw new ProxyError(503, 'Session capacity reached');
    const session = Object.freeze({ id: `session_${randomUUID()}`, principal, routeId, variables: Object.freeze(variables.map(v => Object.freeze({ ...v }))), createdAt: now, expiresAt: now + this.ttlMs });
    this.sessions.set(session.id, session);
    return session;
  }
  get(id: string, principal: string): Session {
    const session = this.sessions.get(id);
    if (session && session.expiresAt <= this.now()) this.sessions.delete(id);
    if (!session || session.expiresAt <= this.now() || session.principal !== principal) throw new ProxyError(404, 'Unknown or expired session');
    return session;
  }
  clear(): void { this.sessions.clear(); }
}
