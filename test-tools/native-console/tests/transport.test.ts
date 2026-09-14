// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, readSSE } from "../src/api";
import { ChatStream } from "../src/chat";

afterEach(() => vi.unstubAllGlobals());
const bytes = (data: string, width = 1) => {
  const encoded = new TextEncoder().encode(data);
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < encoded.length; i += width)
        controller.enqueue(encoded.slice(i, i + width));
      controller.close();
    },
  });
};

describe("QwenPaw stream transport", () => {
  it("preserves split UTF-8, CRLF, comments and multiline data", async () => {
    const events: unknown[] = [];
    await readSSE(
      bytes(
        ": heartbeat\r\nevent: custom\r\ndata: 你好\r\ndata: world\r\n\r\ndata: last\n\n",
      ),
      (data, event) => events.push({ data, event }),
    );
    expect(events).toEqual([
      { data: "你好\nworld", event: "custom" },
      { data: "last", event: "message" },
    ]);
  });
  it("reconciles delta, content snapshot, message snapshot, and final output", () => {
    const stream = new ChatStream();
    stream.consume({
      object: "content",
      msg_id: "a",
      index: 0,
      delta: true,
      text: "你",
    });
    stream.consume({
      object: "content",
      msg_id: "a",
      index: 0,
      delta: true,
      text: "好",
    });
    stream.consume({
      object: "content",
      msg_id: "a",
      index: 0,
      delta: false,
      text: "你好",
    });
    expect(stream.text).toBe("你好");
    stream.consume({
      object: "message",
      id: "a",
      message: { role: "assistant", content: [{ type: "text", text: "你好" }] },
    });
    expect(stream.text).toBe("你好");
    stream.consume({
      object: "response",
      status: "completed",
      output: [
        {
          id: "a",
          role: "assistant",
          content: [{ type: "text", text: "你好！" }],
        },
      ],
    });
    expect(stream.text).toBe("你好！");
    expect(stream.complete).toBe(true);
  });
  it("keeps an early EOF incomplete and reports stream errors", () => {
    const stream = new ChatStream();
    stream.consume({ object: "content", text: "partial", delta: true });
    expect(stream.complete).toBe(false);
    stream.consume({ error: "quota exceeded" });
    expect(stream.error).toBe("quota exceeded");
  });
  it("logs HTTP errors without exposing request or response credentials", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              password: "pw-value",
              detail: "Bearer api-token rejected",
            }),
            { status: 401 },
          ),
      ),
    );
    const api = new ApiClient();
    api.token = "api-token";
    api.agent = "agent-two";
    await expect(
      api.request("/api/auth/login", {
        method: "POST",
        body: { password: "pw-value" },
      }),
    ).rejects.toThrow("HTTP 401");
    const log = JSON.stringify(api.logs);
    expect(log).not.toContain("pw-value");
    expect(log).not.toContain("api-token");
    expect(log).toContain("[REDACTED]");
    expect(api.logs[0].status).toBe(401);
    expect(api.logs[0].duration).toBeGreaterThanOrEqual(0);
    const call = vi.mocked(fetch).mock.calls[0];
    expect(new Headers(call[1]?.headers).get("X-Agent-Id")).toBe("agent-two");
  });
  it("retains unknown SSE events and caps event history", async () => {
    const response = Array.from(
      { length: 105 },
      (_, i) => `data: ${JSON.stringify({ type: "tool", index: i })}\n\n`,
    ).join("");
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(bytes(response, 79), {
            headers: { "Content-Type": "text/event-stream" },
          }),
      ),
    );
    const api = new ApiClient();
    let count = 0;
    await api.request("/api/console/chat", {
      method: "POST",
      onEvent: () => count++,
    });
    expect(count).toBe(105);
    expect(api.logs[0].events).toHaveLength(100);
    expect(api.logs[0].eventCount).toBe(105);
  });
  it("records network cancellation and rejects requests outside /api/", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_path, init) =>
          new Promise((_resolve, reject) => {
            (init!.signal as AbortSignal).addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          }),
      ),
    );
    const api = new ApiClient();
    const controller = new AbortController();
    const pending = api.request("/api/console/chat", {
      signal: controller.signal,
    });
    controller.abort();
    await expect(pending).rejects.toThrow("Aborted");
    expect(api.logs[0].status).toBe("aborted");
    await expect(api.request("https://example.org")).rejects.toThrow(
      "Only same-origin",
    );
  });
  it("downloads binary bodies without logging their content", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(new Uint8Array([0, 255, 42]))),
    );
    const api = new ApiClient();
    const blob = await api.request<Blob>(
      "/api/workspace/file-download?path=x",
      { blob: true },
    );
    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual([0, 255, 42]);
    expect(api.logs[0].response).toMatchObject({ size: 3 });
  });
});
