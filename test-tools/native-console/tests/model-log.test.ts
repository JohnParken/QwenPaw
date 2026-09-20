// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../src/api";
import { isModelLogEvent, ModelLogStore } from "../src/model-log";
import { ServiceClient } from "../src/service";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const event = (id: string, payload: unknown = { text: "safe" }) => ({
  type: "model_log",
  object: "diagnostic",
  id,
  event: "request",
  level: "DEBUG",
  payload,
  timestamp: "2026-09-18T00:00:00Z",
  run_id: "run-1",
  invocation_id: "inv-1",
});

describe("model diagnostic storage", () => {
  it("accepts diagnostic envelopes, redacts payloads, deduplicates ids, and caps entries", () => {
    const api = new ApiClient();
    api.remember({ api_key: "secret-value" });
    const store = new ModelLogStore(3);

    expect(isModelLogEvent(event("one"))).toBe(true);
    expect(
      store.add(
        event("one", { api_key: "secret-value" }),
        "local",
        api.safe.bind(api),
      ),
    ).toBe(true);
    expect(
      store.add(
        event("one", { api_key: "secret-value" }),
        "local",
        api.safe.bind(api),
      ),
    ).toBe(false);
    expect(JSON.stringify(store.entries)).not.toContain("secret-value");
    expect(store.entries[0].source_id).toBe("one");

    store.add(event("two"), "local");
    store.add(event("three"), "service");
    store.add(event("four"), "service");
    expect(store.entries).toHaveLength(3);
    expect(store.entries.map((item) => item.source_id)).toEqual([
      "four",
      "three",
      "two",
    ]);
  });

  it("bounds oversized payloads while retaining the truncation marker", () => {
    const store = new ModelLogStore();
    expect(
      store.add(event("large", { body: "x".repeat(100_000) }), "service"),
    ).toBe(true);
    expect(store.entries[0].truncated).toBe(true);
    expect(JSON.stringify(store.entries[0].payload).length).toBeLessThan(
      50_000,
    );
  });
});

describe("diagnostic transport controls", () => {
  it("sends service debug only when enabled", async () => {
    const fetchMock = vi.fn(
      async (_path, init) =>
        new Response(JSON.stringify({ id: "run-1" }), {
          status: 202,
          headers: { "content-type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const service = new ServiceClient(new ApiClient(), { user: "tester" });
    await service.submit("session", "hello", [], "request-off", false);
    await service.submit("session", "hello", [], "request-on", true);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
      request_id: "request-off",
    });
    expect(
      JSON.parse(String(fetchMock.mock.calls[0][1]?.body)).debug,
    ).toBeUndefined();
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toMatchObject({
      request_id: "request-on",
      debug: true,
    });
  });

  it("logs safe SSE traces only in verbose mode and redacts errors", async () => {
    const debug = vi.spyOn(console, "debug").mockImplementation(() => {});
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            'data: {"type":"model_log","event":"request","payload":{"token":"secret-token"}}\n\n',
            { headers: { "content-type": "text/event-stream" } },
          ),
      ),
    );
    const api = new ApiClient();
    api.remember({ token: "secret-token" });
    api.setVerboseLogging(true);
    await api.request("/api/console/chat", { onEvent: () => {} });
    expect(debug).toHaveBeenCalled();
    expect(JSON.stringify(debug.mock.calls)).not.toContain("secret-token");

    vi.mocked(fetch).mockRejectedValueOnce(new Error("secret-token failed"));
    await expect(api.request("/api/console/chat")).rejects.toThrow();
    expect(error).toHaveBeenCalled();
    expect(JSON.stringify(error.mock.calls)).not.toContain("secret-token");
  });
});

it("reports inactivity timeouts as errors, while keeping user cancellation quiet", async () => {
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          const signal = init.signal as AbortSignal;
          if (signal.aborted) reject(signal.reason);
          else
            signal.addEventListener("abort", () => reject(signal.reason), {
              once: true,
            });
        }),
    ),
  );
  const api = new ApiClient();
  await expect(
    api.request("/api/console/chat", { timeoutMs: 5 }),
  ).rejects.toThrow("请求超时");
  expect(api.logs[0].status).toBe("timeout");
  expect(error).toHaveBeenCalledOnce();
  const cancel = new AbortController();
  cancel.abort(new Error("user cancelled"));
  await expect(
    api.request("/api/console/chat", { signal: cancel.signal }),
  ).rejects.toThrow();
  expect(error).toHaveBeenCalledOnce();
});
