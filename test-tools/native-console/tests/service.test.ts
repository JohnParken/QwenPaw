// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../src/api";
import { ChatStream } from "../src/chat";
import { ServiceClient } from "../src/service";

afterEach(() => vi.unstubAllGlobals());

const streamResponse = (body: string) =>
  new Response(body, {
    headers: { "content-type": "text/event-stream" },
  });

describe("service streaming contract", () => {
  it("submits with the fixed principal but never forwards a browser token", async () => {
    const fetchMock = vi.fn(async (_path, init) => {
      expect(new Headers(init?.headers).get("authorization")).toBeNull();
      return new Response(JSON.stringify({ id: "run-1" }), {
        status: 202,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const api = new ApiClient();
    api.token = "browser-management-token";
    const service = new ServiceClient(api, { user: "dev-user" });
    await expect(service.submit("session-1", "hello")).resolves.toMatchObject({
      id: "run-1",
    });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
      usrid: "dev-user",
    });
  });

  it("reconnects with Last-Event-ID and de-duplicates replayed ids", async () => {
    const cursors: string[] = [];
    let connection = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_path, init) => {
        const headers = new Headers(init?.headers);
        cursors.push(headers.get("Last-Event-ID") || "");
        connection++;
        if (connection === 1)
          return streamResponse('id: 1\ndata: {"type":"text","text":"one"}\n\n');
        return streamResponse(
          'id: 1\ndata: {"type":"text","text":"duplicate"}\n\n' +
            'id: 2\ndata: {"type":"tool","tool_call_id":"t1","name":"shell","status":"running"}\n\n' +
            'event: end\ndata: {"status":"completed"}\n\n',
        );
      }),
    );
    const api = new ApiClient();
    const service = new ServiceClient(api, {
      user: "dev-user",
      reconnectAttempts: 2,
    });
    const events: unknown[] = [];
    const result = await service.stream(
      "run-1",
      { onEvent: (event) => events.push(event) },
      new AbortController().signal,
    );
    expect(cursors).toEqual(["0", "1"]);
    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({ text: "one" });
    expect(events[1]).toMatchObject({ tool_call_id: "t1" });
    expect(result).toEqual({ cursor: 2, status: "completed" });
  });
});

describe("ordered service timeline", () => {
  it("keeps text, previews, and canonical tool state ordered and stable", () => {
    const stream = new ChatStream({
      completeOnResponse: false,
      orderedTimeline: true,
    });
    stream.consume({ type: "text", text: "before", delta: true });
    stream.consume({
      type: "tool",
      tool_call_id: "call-1",
      name: "shell",
      arguments: { command: "echo hi" },
      status: "preparing",
    });
    stream.consume({
      type: "approval",
      id: "approval-1",
      tool_call_id: "call-1",
      name: "shell",
      arguments: { command: "echo hi" },
    });
    stream.consume({
      type: "tool_output",
      tool_call_id: "call-1",
      text: "first",
    });
    stream.consume({
      type: "tool_output",
      tool_call_id: "call-1",
      text: " second",
    });
    stream.consume({
      type: "preview_start",
      run_id: "r",
      invocation_id: "i",
      attempt_id: "a",
    });
    stream.consume({
      type: "preview_update",
      run_id: "r",
      invocation_id: "i",
      attempt_id: "a",
      item_index: 0,
      kind: "tool_call",
      text: "{\"command\":\"echo hi\"}",
    });
    stream.consume({
      type: "tool",
      tool_call_id: "call-1",
      name: "shell",
      arguments: { command: "echo hi" },
      status: "failed",
    });
    stream.consume({
      object: "response",
      status: "completed",
      output: [{ type: "tool_call", id: "call-1", name: "shell" }],
    });

    expect(stream.timeline.map((item) => item.kind)).toEqual([
      "text",
      "tool",
      "preview",
    ]);
    expect(stream.toolMap.get("call-1")?.output).toBe("first second");
    expect(stream.toolMap.get("call-1")?.status).toBe("failed");
    expect(stream.pendingApproval?.toolCallId).toBe("call-1");
    expect(stream.complete).toBe(false);
  });

  it("does not double append runtime content text envelopes", () => {
    const stream = new ChatStream();
    stream.consume({
      object: "content",
      type: "text",
      msg_id: "message-1",
      index: 0,
      delta: true,
      text: "hello",
    });
    expect(stream.text).toBe("hello");
    expect(stream.timeline.filter((item) => item.kind === "text")).toHaveLength(1);
  });

  it("keeps partial tool metadata, structured output, and bounded logs", () => {
    const stream = new ChatStream();
    stream.consume({
      type: "tool",
      tool_call_id: "call-1",
      name: "shell",
      arguments: { command: "echo hi" },
      status: "running",
    });
    stream.consume({ type: "tool", tool_call_id: "call-1", status: "failed" });
    stream.consume({
      type: "tool",
      tool_call_id: "call-1",
      output: { exit_code: 1, stderr: "failed" },
    });
    const item = stream.toolMap.get("call-1")!;
    expect(item.name).toBe("shell");
    expect(item.args).toContain('"command": "echo hi"');
    expect(item.output).toContain('"exit_code": 1');
    expect(stream.error).toBe("");

    stream.consume({
      type: "tool_output",
      tool_call_id: "call-1",
      text: "x".repeat(70_000),
    });
    expect(item.output).toHaveLength(65_536);
    expect(item.output?.endsWith("x".repeat(65_536))).toBe(true);
  });

  it("handles approval decisions by nested call_id and clears pending state", () => {
    const stream = new ChatStream();
    stream.consume({
      type: "approval",
      tool_call_id: "call-approval",
      name: "shell",
      arguments: { command: "pwd" },
      approval: { id: "approval-1", call_id: "call-approval", status: "pending" },
    });
    expect(stream.pendingApproval?.toolCallId).toBe("call-approval");
    stream.consume({
      type: "approval",
      approval: { id: "approval-1", call_id: "call-approval", status: "approved" },
    });
    expect(stream.pendingApproval).toBeNull();
    expect(stream.toolMap.get("call-approval")?.status).toBe("running");
  });

  it("folds runtime data function calls and keeps canonical service status", () => {
    const stream = new ChatStream({ completeOnResponse: false });
    stream.consume({
      object: "content",
      type: "data",
      msg_id: "runtime-message",
      delta: true,
      data: { call_id: "runtime-call", name: "shell", arguments: "echo " },
    });
    stream.consume({
      object: "content",
      type: "data",
      msg_id: "runtime-message",
      delta: true,
      data: { arguments: "hi" },
    });
    expect(stream.toolMap.get("runtime-call")).toMatchObject({
      name: "shell",
      args: "echo hi",
      status: "preparing",
    });
    stream.consume({
      type: "tool",
      tool_call_id: "runtime-call",
      name: "shell",
      arguments: { command: "echo hi" },
      status: "running",
    });
    stream.consume({
      object: "response",
      status: "completed",
      output: [
        { type: "tool_call", id: "runtime-call", name: "shell", arguments: "echo hi" },
      ],
    });
    expect(stream.toolMap.get("runtime-call")?.status).toBe("running");
  });

  it("clears every preview item for an attempt and on terminal", () => {
    const stream = new ChatStream({ completeOnResponse: false, orderedTimeline: true });
    const base = {
      run_id: "run-1",
      invocation_id: "invocation-1",
      attempt_id: "attempt-1",
    };
    stream.consume({ type: "preview_start", ...base });
    stream.consume({ type: "preview_update", ...base, item_index: 0, text: "a" });
    stream.consume({ type: "preview_update", ...base, item_index: 1, text: "b" });
    stream.consume({ type: "preview_clear", ...base, reason: "commit" });
    expect(stream.timeline.some((item) => item.kind === "preview")).toBe(false);

    stream.consume({ type: "preview_start", ...base, attempt_id: "attempt-2" });
    stream.consume({
      type: "preview_update",
      ...base,
      attempt_id: "attempt-2",
      item_index: 0,
      text: "c",
    });
    stream.markTerminal("completed");
    expect(stream.timeline.some((item) => item.kind === "preview")).toBe(false);
  });
});
