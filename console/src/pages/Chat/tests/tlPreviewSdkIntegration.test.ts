import { expect, it } from "vitest";
import Builder from "@agentscope-ai/chat/lib/AgentScopeRuntimeWebUI/core/AgentScopeRuntime/Response/Builder.js";
import { consumeTLPreviewEvent, createTLPreviewStore } from "../tlPreview";

it("keeps drafts outside the real SDK builder and commits the answer once", () => {
  const builder = new Builder({
    id: "response",
    status: "created",
    created_at: 1,
  } as ConstructorParameters<typeof Builder>[0]);
  let store = createTLPreviewStore();
  const identity = {
    run_id: "run",
    invocation_id: "invocation",
    attempt_id: "attempt",
  };
  function parse(payload: Record<string, unknown>) {
    const consumed = consumeTLPreviewEvent(store, payload);
    store = consumed.store;
    return consumed.handled
      ? { object: "message", type: "heartbeat" }
      : payload;
  }
  function feed(payload: Record<string, unknown>) {
    return builder.handle(
      parse(payload) as unknown as Parameters<Builder["handle"]>[0],
    );
  }
  feed({ ...identity, type: "preview_start" });
  feed({
    ...identity,
    type: "preview_update",
    kind: "final_text",
    revision: 1,
    item_index: 0,
    text: "你好",
  });
  expect(Object.values(store.attempts)[0].items[0]).toBe("你好");
  expect(builder.data.output).toEqual([]);
  feed({ ...identity, type: "preview_clear", reason: "commit" });
  expect(store.attempts).toEqual({});
  feed({
    object: "message",
    type: "message",
    id: "message",
    role: "assistant",
    status: "completed",
    content: [{ type: "text", text: "你好", index: 0 }],
  });
  const result = feed({
    object: "response",
    id: "response",
    status: "completed",
    output: [],
  });
  expect(result.status).toBe("completed");
  expect(result.output).toHaveLength(1);
  expect(result.output[0].content).toEqual([
    { type: "text", text: "你好", index: 0 },
  ]);
});
