export interface LogEntry {
  id: number;
  time: string;
  method: string;
  url: string;
  status: number | string;
  duration: number;
  request: unknown;
  response?: unknown;
  events: unknown[];
  eventCount: number;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  onEvent?: (data: unknown, event: string, id?: string) => void;
  blob?: boolean;
  timeoutMs?: number;
  /** Service requests never receive the local-console browser credential. */
  service?: boolean;
}
const secretKey =
  /password|passwd|secret|token|authorization|cookie|api[_-]?key|credential/i;
export class ApiClient {
  token = "";
  agent = "default";
  /** Enables safe console.debug transport traces from the UI Debug toggle. */
  verboseLogging = false;
  logs: LogEntry[] = [];
  onLog: () => void = () => {};
  private serial = 0;
  private secrets = new Set<string>();
  remember(value: unknown): void {
    if (!value || typeof value !== "object") return;
    for (const [key, item] of Object.entries(value)) {
      if (secretKey.test(key) && typeof item === "string" && item)
        this.secrets.add(item);
      else this.remember(item);
    }
  }
  safe(value: unknown, depth = 0): unknown {
    if (depth >= 8) return "[depth truncated]";
    if (value instanceof FormData)
      return Array.from(value.entries())
        .slice(0, 150)
        .map(([key, item]) => ({
          field: key,
          value: secretKey.test(key)
            ? "[REDACTED]"
            : item instanceof File
              ? { name: item.name, size: item.size, type: item.type }
              : this.safe(item, depth + 1),
        }));
    if (Array.isArray(value))
      return value.slice(0, 150).map((v) => this.safe(v, depth + 1));
    if (value && typeof value === "object")
      return Object.fromEntries(
        Object.entries(value)
          .slice(0, 150)
          .map(([k, v]) => [
            k,
            secretKey.test(k) ? "[REDACTED]" : this.safe(v, depth + 1),
          ]),
      );
    if (typeof value === "string") {
      let result = value;
      for (const secret of this.secrets)
        result = result.split(secret).join("[REDACTED]");
      if (this.token) result = result.split(this.token).join("[REDACTED]");
      return (
        result.slice(0, 12000) + (result.length > 12000 ? "…[truncated]" : "")
      );
    }
    return value;
  }
  clear(): void {
    this.logs = [];
    this.onLog();
  }

  setVerboseLogging(enabled: boolean): void {
    this.verboseLogging = enabled;
  }

  private debug(label: string, value: unknown): void {
    if (!this.verboseLogging) return;
    console.debug(`[native-console] ${label}`, this.safe(value));
  }

  private error(label: string, value: unknown): void {
    console.error(`[native-console] ${label}`, this.safe(value));
  }

  async request<T = unknown>(
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    if (!path.startsWith("/api/") || path.includes("://"))
      throw new Error("Only same-origin /api/ requests are allowed");
    this.remember(options.body);
    const started = performance.now();
    const headers = new Headers(options.headers);
    if (options.service) {
      // A service token belongs to the Vite proxy. Never let a page-local
      // management token or a caller-supplied Authorization header cross the
      // service boundary.
      headers.delete("Authorization");
      headers.delete("authorization");
    } else {
      headers.set("X-Agent-Id", this.agent);
      if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
    }
    let body: BodyInit | undefined;
    if (options.body instanceof FormData) body = options.body;
    else if (options.body !== undefined) {
      body = JSON.stringify(options.body);
      headers.set("Content-Type", "application/json");
    }
    const log: LogEntry = {
      id: ++this.serial,
      time: new Date().toISOString(),
      method: options.method || "GET",
      url: String(this.safe(path)),
      status: "pending",
      duration: 0,
      request: this.safe({
        headers: Object.fromEntries(headers),
        body: options.body,
      }),
      events: [],
      eventCount: 0,
    };
    this.logs.unshift(log);
    this.logs.length = Math.min(this.logs.length, 100);
    this.onLog();
    const controller = new AbortController();
    const abort = () => controller.abort(options.signal?.reason);
    if (options.signal?.aborted) abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    // For streams this is an inactivity timeout, refreshed on every event.
    let timer: ReturnType<typeof setTimeout>;
    let timedOut = false;
    const resetTimer = () => {
      clearTimeout(timer);
      const timeout = options.timeoutMs ?? 60000;
      if (timeout > 0)
        timer = setTimeout(
          () => {
            timedOut = true;
            controller.abort(new Error("请求超时"));
          },
          Math.min(timeout, 2147483647),
        );
    };
    resetTimer();
    try {
      const response = await fetch(path, {
        method: log.method,
        headers,
        body,
        signal: controller.signal,
      });
      log.status = response.status;
      this.debug("response", {
        method: log.method,
        url: path,
        status: response.status,
        contentType: response.headers.get("content-type") || "",
      });
      this.onLog();
      if (options.onEvent && response.ok) {
        if (
          !response.headers.get("content-type")?.includes("text/event-stream")
        )
          throw new Error("Expected text/event-stream response");
        if (!response.body) throw new Error("Response has no stream");
        await readSSE(response.body, (raw, event, id) => {
          resetTimer();
          let data: unknown = raw;
          if (raw !== "[DONE]") {
            try {
              data = JSON.parse(raw);
            } catch {
              /* textual event */
            }
          }
          this.remember(data);
          log.eventCount++;
          log.events.push(this.safe({ event, id, data }));
          if (log.events.length > 100) log.events.shift();
          log.duration = Math.round(performance.now() - started);
          this.onLog();
          this.debug("SSE event", { url: path, event, id, data });
          options.onEvent!(data, event, id);
        });
        log.response = { stream: "closed", eventCount: log.eventCount };
        return undefined as T;
      }
      if (options.blob && response.ok) {
        const data = await response.blob();
        log.response = { size: data.size, type: data.type };
        return data as T;
      }
      const text = await response.text();
      let data: unknown = text;
      if (text) {
        try {
          data = JSON.parse(text);
        } catch {
          /* plain response */
        }
      }
      this.remember(data);
      log.response = this.safe(data);
      if (!response.ok)
        throw new Error(
          `HTTP ${response.status}: ${JSON.stringify(this.safe(data))}`,
        );
      return data as T;
    } catch (error) {
      if (timedOut) log.status = "timeout";
      else if (controller.signal.aborted) log.status = "aborted";
      else if (log.status === "pending") log.status = "network-error";
      log.response = this.safe({
        error: error instanceof Error ? error.message : String(error),
      });
      if (timedOut || !controller.signal.aborted)
        this.error("request failed", {
          method: log.method,
          url: path,
          status: log.status,
          error: error instanceof Error ? error.message : String(error),
        });
      throw error;
    } finally {
      clearTimeout(timer!);
      options.signal?.removeEventListener("abort", abort);
      log.duration = Math.round(performance.now() - started);
      this.onLog();
    }
  }

  serviceRequest<T = unknown>(
    path: string,
    options: Omit<RequestOptions, "service"> = {},
  ): Promise<T> {
    if (!path.startsWith("/"))
      throw new Error("Service paths must start with '/'");
    return this.request<T>(`/api/service${path}`, {
      ...options,
      service: true,
    });
  }
}

// Handles CR/LF/CRLF across byte chunks, UTF-8 boundaries and multi-line data.
export async function readSSE(
  stream: ReadableStream<Uint8Array>,
  emit: (data: string, event: string, id?: string) => void,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lines: string[] = [];
  let event = "message";
  let eventId: string | undefined;
  const dispatch = () => {
    if (lines.length) emit(lines.join("\n"), event, eventId);
    lines = [];
    event = "message";
  };
  const line = (value: string) => {
    if (!value) return dispatch();
    if (value.startsWith(":")) return;
    const colon = value.indexOf(":");
    const field = colon < 0 ? value : value.slice(0, colon);
    const raw = colon < 0 ? "" : value.slice(colon + 1);
    const data = raw.startsWith(" ") ? raw.slice(1) : raw;
    if (field === "data") lines.push(data);
    if (field === "event") event = data;
    if (field === "id" && !data.includes("\0")) eventId = data;
  };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      buffer += done
        ? decoder.decode()
        : decoder.decode(value, { stream: true });
      let index: number;
      while ((index = buffer.search(/[\r\n]/)) >= 0) {
        if (!done && buffer[index] === "\r" && index === buffer.length - 1)
          break;
        const width =
          buffer[index] === "\r" && buffer[index + 1] === "\n" ? 2 : 1;
        line(buffer.slice(0, index));
        buffer = buffer.slice(index + width);
      }
      if (buffer.length > 2 * 1024 * 1024)
        throw new Error("SSE event exceeds 2 MiB");
      if (done) {
        if (buffer) line(buffer);
        dispatch();
        break;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
