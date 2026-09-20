import { ApiClient } from "./api";

export interface ServiceSession {
  id: string;
  external_id?: string;
  channel_id?: string;
  state?: string;
  updated_at?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface ServiceRun {
  id: string;
  session_id?: string;
  status?: string;
  request_id?: string;
  [key: string]: unknown;
}

export interface ServiceMessageRow {
  seq?: number;
  run_id?: string;
  payload?: Record<string, unknown>;
  role?: string;
  content?: unknown;
  [key: string]: unknown;
}

export interface ServiceStreamHandlers {
  onEvent: (payload: unknown, event: string, seq?: number) => void;
  onReconnect?: (attempt: number, cursor: number) => void;
  onEnd?: (payload: unknown) => void;
}

export interface ServiceStreamResult {
  cursor: number;
  status: string;
}

export interface ServiceClientOptions {
  user: string;
  channel?: string;
  reconnectAttempts?: number;
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted)
    return Promise.reject(signal.reason || new Error("Aborted"));
  return new Promise((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout>;
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason || new Error("Aborted"));
    };
    timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function aborted(signal: AbortSignal): boolean {
  return (
    signal.aborted ||
    (signal.reason instanceof DOMException &&
      signal.reason.name === "AbortError")
  );
}

export class ServiceClient {
  readonly user: string;
  readonly channel: string;
  private readonly reconnectAttempts: number;

  constructor(
    private readonly api: ApiClient,
    options: ServiceClientOptions,
  ) {
    if (!options.user.trim()) throw new Error("Service user is required");
    this.user = options.user;
    this.channel = options.channel || "native-console";
    this.reconnectAttempts = Math.max(0, options.reconnectAttempts ?? 5);
  }

  async health(): Promise<Record<string, unknown>> {
    return this.api.serviceRequest<Record<string, unknown>>("/health");
  }

  async assistant(): Promise<Record<string, unknown>> {
    return this.api.serviceRequest<Record<string, unknown>>("/assistant");
  }

  async sessions(limit = 100): Promise<ServiceSession[]> {
    const result = await this.api.serviceRequest<unknown>(
      `/sessions?limit=${Math.max(1, Math.min(100, limit))}`,
    );
    const rows = Array.isArray(result)
      ? result
      : (result as Record<string, unknown>)?.sessions;
    return Array.isArray(rows)
      ? rows.filter((item): item is ServiceSession =>
          Boolean(item && typeof item === "object"),
        )
      : [];
  }

  async submit(
    sessionId: string,
    message: string,
    attachments: string[] = [],
    requestId = crypto.randomUUID(),
    debug = false,
  ): Promise<ServiceRun> {
    return this.api.serviceRequest<ServiceRun>("/runs", {
      method: "POST",
      body: {
        usrid: this.user,
        sessionid: sessionId,
        channelid: this.channel,
        request_id: requestId,
        message,
        attachments,
        ...(debug === true ? { debug: true } : {}),
      },
    });
  }

  async upload(file: File): Promise<Record<string, unknown>> {
    const form = new FormData();
    form.append("file", file);
    return this.api.serviceRequest<Record<string, unknown>>("/files", {
      method: "POST",
      body: form,
    });
  }

  async file(fileId: string): Promise<Record<string, unknown>> {
    return this.api.serviceRequest<Record<string, unknown>>(
      `/files/${encode(fileId)}`,
    );
  }

  async download(fileId: string): Promise<Blob> {
    return this.api.serviceRequest<Blob>(`/files/${encode(fileId)}/content`, {
      blob: true,
    });
  }

  async history(
    sessionId: string,
    after = 0,
    limit = 100,
  ): Promise<ServiceMessageRow[]> {
    const result = await this.api.serviceRequest<unknown>(
      `/sessions/${encode(sessionId)}/messages?limit=${Math.max(1, Math.min(100, limit))}&after=${Math.max(0, after)}`,
    );
    const rows = Array.isArray(result)
      ? result
      : (result as Record<string, unknown>)?.messages;
    return Array.isArray(rows)
      ? rows.filter((item): item is ServiceMessageRow =>
          Boolean(item && typeof item === "object"),
        )
      : [];
  }

  async getRun(runId: string): Promise<ServiceRun> {
    return this.api.serviceRequest<ServiceRun>(`/runs/${encode(runId)}`);
  }

  async cancel(runId: string): Promise<Record<string, unknown>> {
    return this.api.serviceRequest<Record<string, unknown>>(
      `/runs/${encode(runId)}/cancel`,
      {
        method: "POST",
        body: {},
      },
    );
  }

  async decide(
    approvalId: string,
    approved: boolean,
  ): Promise<Record<string, unknown>> {
    return this.api.serviceRequest<Record<string, unknown>>(
      `/approvals/${encode(approvalId)}/decision`,
      { method: "POST", body: { approved } },
    );
  }

  async stream(
    runId: string,
    handlers: ServiceStreamHandlers,
    signal: AbortSignal,
    initialCursor = 0,
  ): Promise<ServiceStreamResult> {
    let cursor = initialCursor;
    let status = "";
    let done = false;
    let retry = 0;
    while (!done && !aborted(signal) && retry <= this.reconnectAttempts) {
      try {
        await this.api.serviceRequest<void>(`/runs/${encode(runId)}/events`, {
          headers: {
            Accept: "text/event-stream",
            "Last-Event-ID": String(cursor),
          },
          signal,
          timeoutMs: 0,
          onEvent: (payload, event, rawId) => {
            if (event === "end") {
              const end =
                payload && typeof payload === "object"
                  ? (payload as Record<string, unknown>)
                  : {};
              status = String(end.status || "unknown");
              if (
                !["completed", "failed", "cancelled", "interrupted"].includes(
                  status,
                )
              )
                throw new Error("Invalid service terminal status");
              done = true;
              handlers.onEnd?.(payload);
              return;
            }
            let seq: number | undefined;
            if (rawId !== undefined && rawId !== "") {
              seq = Number(rawId);
              if (!Number.isSafeInteger(seq) || seq < 0)
                throw new Error("Invalid service event cursor");
              if (seq <= cursor) return;
            }
            handlers.onEvent(payload, event, seq);
            if (seq !== undefined) cursor = seq;
          },
        });
      } catch (error) {
        if (aborted(signal)) throw error;
        retry++;
        if (retry > this.reconnectAttempts) throw error;
        handlers.onReconnect?.(retry, cursor);
        await sleep(Math.min(500 * retry, 2000), signal);
        continue;
      }
      if (!done && !aborted(signal)) {
        retry++;
        if (retry > this.reconnectAttempts)
          throw new Error("服务流重连次数已用尽");
        handlers.onReconnect?.(retry, cursor);
        await sleep(Math.min(500 * retry, 2000), signal);
      }
    }
    if (aborted(signal)) throw signal.reason || new Error("Aborted");
    return { cursor, status: status || "completed" };
  }
}

export function messagePayload(
  row: ServiceMessageRow,
): Record<string, unknown> {
  return row.payload && typeof row.payload === "object"
    ? row.payload
    : (row as Record<string, unknown>);
}

export function isRunTimeline(payload: Record<string, unknown>): boolean {
  return (
    payload.role === "assistant" &&
    payload.type === "run_timeline" &&
    Array.isArray(payload.events)
  );
}
