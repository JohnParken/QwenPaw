export type ModelLogMode = "local" | "service";

export interface ModelLogEnvelope {
  type: "model_log";
  object?: "diagnostic" | string;
  event?: string;
  level?: string;
  payload?: unknown;
  truncated?: boolean;
  timestamp?: string;
  id?: string;
  run_id?: string;
  invocation_id?: string;
  request_id?: string;
  attempt_id?: string;
  [key: string]: unknown;
}

export interface ModelLogEntry {
  id: string;
  mode: ModelLogMode;
  type: "model_log";
  object: string;
  event: string;
  level: string;
  payload: unknown;
  truncated: boolean;
  source_id?: string;
  timestamp: string;
  received_at: string;
  run_id: string;
  invocation_id: string;
  request_id?: string;
  attempt_id?: string;
}

export type ModelLogSanitizer = (value: unknown) => unknown;

const DEFAULT_LIMIT = 200;
const MAX_PAYLOAD_CHARS = 48_000;
const MAX_NESTING = 8;
const MAX_ITEMS = 150;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

export function isModelLogEvent(value: unknown): value is ModelLogEnvelope {
  return (
    isRecord(value) &&
    value.type === "model_log" &&
    (value.object === undefined || value.object === "diagnostic")
  );
}

function json(value: unknown): string {
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

function boundedValue(value: unknown, depth = 0): unknown {
  if (depth >= MAX_NESTING) return "[depth truncated]";
  if (typeof value === "string")
    return value.length > 12_000 ? `${value.slice(0, 11_999)}…` : value;
  if (Array.isArray(value))
    return value
      .slice(0, MAX_ITEMS)
      .map((item) => boundedValue(item, depth + 1));
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.entries(value)
        .slice(0, MAX_ITEMS)
        .map(([key, item]) => [key, boundedValue(item, depth + 1)]),
    );
  }
  return value;
}

function boundedPayload(value: unknown): {
  value: unknown;
  truncated: boolean;
} {
  const original = json(value);
  const bounded = boundedValue(value);
  const encoded = json(bounded);
  if (encoded.length <= MAX_PAYLOAD_CHARS && encoded.length === original.length)
    return { value: bounded, truncated: false };
  return {
    value:
      encoded.length <= MAX_PAYLOAD_CHARS
        ? bounded
        : encoded.slice(0, MAX_PAYLOAD_CHARS - 32),
    truncated: true,
  };
}

function identity(
  envelope: ModelLogEnvelope,
  mode: ModelLogMode,
  payload: unknown,
): string {
  // Backend identifiers make reconnect replay keys stable. The payload is a
  // fallback for older emitters that omit an attempt or request identifier.
  if (envelope.id) return `${mode}:${String(envelope.id)}`;
  const raw = [
    mode,
    envelope.run_id || "",
    envelope.invocation_id || "",
    envelope.request_id || "",
    envelope.attempt_id || "",
    envelope.event || "",
    envelope.timestamp || "",
    json(payload),
  ].join("\u001f");
  let hash = 2166136261;
  for (let index = 0; index < raw.length; index++) {
    hash ^= raw.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `${mode}:${(hash >>> 0).toString(16)}`;
}

export class ModelLogStore {
  readonly limit: number;
  private readonly seen = new Set<string>();
  private items: ModelLogEntry[] = [];

  constructor(limit = DEFAULT_LIMIT) {
    this.limit = Math.max(1, Math.floor(limit));
  }

  get entries(): readonly ModelLogEntry[] {
    return this.items;
  }

  get size(): number {
    return this.items.length;
  }

  add(
    value: unknown,
    mode: ModelLogMode,
    sanitize: ModelLogSanitizer = (item) => item,
  ): boolean {
    if (!isModelLogEvent(value)) return false;
    const payloadResult = boundedPayload(sanitize(value.payload));
    const payload = payloadResult.value;
    const key = identity(value, mode, payload);
    if (this.seen.has(key)) return false;
    const entry: ModelLogEntry = {
      id: key,
      mode,
      type: "model_log",
      object: String(value.object || "diagnostic"),
      event: String(value.event || "unknown"),
      level: String(value.level || "info"),
      payload,
      truncated: value.truncated === true || payloadResult.truncated,
      ...(value.id ? { source_id: String(value.id) } : {}),
      timestamp: String(value.timestamp || ""),
      received_at: new Date().toISOString(),
      run_id: String(value.run_id || ""),
      invocation_id: String(value.invocation_id || ""),
      ...(value.request_id ? { request_id: String(value.request_id) } : {}),
      ...(value.attempt_id ? { attempt_id: String(value.attempt_id) } : {}),
    };
    this.seen.add(key);
    this.items.unshift(entry);
    while (this.items.length > this.limit) {
      const removed = this.items.pop();
      if (removed) this.seen.delete(removed.id);
    }
    return true;
  }

  clear(): void {
    this.items = [];
    this.seen.clear();
  }

  matches(entry: ModelLogEntry, filter: string): boolean {
    const query = filter.trim().toLowerCase();
    if (!query) return true;
    return json(entry).toLowerCase().includes(query);
  }
}
