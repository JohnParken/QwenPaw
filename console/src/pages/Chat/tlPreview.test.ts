import { describe, expect, it, vi } from "vitest";
import {
  consumeTLPreviewEvent,
  createTLPreviewStore,
  wrapTLPreviewLifecycle,
} from "./tlPreview";

it("clears preview on EOF and preserves response bytes", async () => {
  const finish = vi.fn();
  const wrapped = wrapTLPreviewLifecycle(new Response("hello"), finish);
  expect(await wrapped.text()).toBe("hello");
  expect(finish).toHaveBeenCalledTimes(1);
});

it("clears preview on a response body error", async () => {
  const finish = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      controller.error(new Error("broken stream"));
    },
  });
  const wrapped = wrapTLPreviewLifecycle(new Response(body), finish);
  await expect(wrapped.text()).rejects.toThrow("broken stream");
  expect(finish).toHaveBeenCalledTimes(1);
});

it("clears preview once when an aborted body is later canceled", async () => {
  const finish = vi.fn();
  const abort = new AbortController();
  const wrapped = wrapTLPreviewLifecycle(
    new Response(new ReadableStream<Uint8Array>()),
    finish,
    abort.signal,
  );
  abort.abort();
  await wrapped.body!.cancel();
  expect(finish).toHaveBeenCalledTimes(1);
});

const identity = {
  run_id: "run-1",
  invocation_id: "inv-1",
  attempt_id: "attempt-1",
};

function consume(
  store: ReturnType<typeof createTLPreviewStore>,
  payload: object,
) {
  return consumeTLPreviewEvent(store, { ...identity, ...payload });
}

describe("TL preview state", () => {
  it("keeps cumulative snapshots and rejects stale revisions", () => {
    let store = createTLPreviewStore();
    store = consume(store, { type: "preview_start" }).store;
    store = consume(store, {
      type: "preview_update",
      revision: 2,
      kind: "final_text",
      item_index: 0,
      text: "hello",
    }).store;
    const stale = consume(store, {
      type: "preview_update",
      revision: 1,
      kind: "final_text",
      item_index: 0,
      text: "h",
    });

    expect(stale.store).toBe(store);
    expect(Object.values(store.attempts)[0]?.items[0]).toBe("hello");
  });

  it("drops late updates after clear and handles a new attempt", () => {
    let store = createTLPreviewStore();
    store = consume(store, { type: "preview_start" }).store;
    store = consume(store, {
      type: "preview_update",
      revision: 1,
      kind: "tool_call",
      item_index: 0,
      text: "draft",
    }).store;
    store = consume(store, {
      type: "preview_clear",
      reason: "commit",
    }).store;
    const late = consume(store, {
      type: "preview_update",
      revision: 2,
      kind: "tool_call",
      item_index: 0,
      text: "late",
    });

    expect(late.store).toBe(store);
    expect(Object.keys(store.attempts)).toHaveLength(0);
    expect(store.closed).toHaveProperty("run-1\u0000inv-1\u0000attempt-1");
  });

  it("filters malformed preview events from the builder", () => {
    const result = consumeTLPreviewEvent(createTLPreviewStore(), {
      type: "preview_update",
      ...identity,
      revision: "1",
      kind: "final_text",
      item_index: 0,
      text: "ignored",
    });

    expect(result.handled).toBe(true);
    expect(result.store).toEqual(createTLPreviewStore());
  });
});
