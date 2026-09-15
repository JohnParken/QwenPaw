export const TL_PREVIEW_CAPABILITY = "tl_preview" as const;

export type TLPreviewEventType =
  | "preview_start"
  | "preview_update"
  | "preview_clear";

export type TLPreviewKind = "final_text" | "tool_call";

export type TLPreviewPayload = {
  type: TLPreviewEventType;
  run_id?: unknown;
  invocation_id?: unknown;
  attempt_id?: unknown;
  revision?: unknown;
  kind?: unknown;
  item_index?: unknown;
  text?: unknown;
  reason?: unknown;
};

export type TLPreviewAttempt = {
  runId: string;
  invocationId: string;
  attemptId: string;
  revision: number;
  kind: TLPreviewKind | null;
  items: Record<number, string>;
};

export type TLPreviewStore = {
  attempts: Record<string, TLPreviewAttempt>;
  closed: Record<string, true>;
};

export type TLPreviewConsumeResult = {
  handled: boolean;
  store: TLPreviewStore;
};

const EVENT_TYPES = new Set<TLPreviewEventType>([
  "preview_start",
  "preview_update",
  "preview_clear",
]);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);

const nonEmptyString = (value: unknown): value is string =>
  typeof value === "string" && value.length > 0;

const attemptKey = (runId: string, invocationId: string, attemptId: string) =>
  `${runId}\u0000${invocationId}\u0000${attemptId}`;

const eventKey = (payload: TLPreviewPayload): string | null => {
  if (
    !nonEmptyString(payload.run_id) ||
    !nonEmptyString(payload.invocation_id) ||
    !nonEmptyString(payload.attempt_id)
  ) {
    return null;
  }
  return attemptKey(payload.run_id, payload.invocation_id, payload.attempt_id);
};

export function createTLPreviewStore(): TLPreviewStore {
  return { attempts: {}, closed: {} };
}

/** Clear ephemeral UI state on EOF, reader failure, cancellation or abort. */
export function wrapTLPreviewLifecycle(
  response: Response,
  onFinish: () => void,
  signal?: AbortSignal,
): Response {
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    signal?.removeEventListener("abort", finish);
    onFinish();
  };
  if (!response.body) {
    finish();
    return response;
  }
  signal?.addEventListener("abort", finish, { once: true });
  if (signal?.aborted) finish();
  const reader = response.body.getReader();
  return new Response(
    new ReadableStream<Uint8Array>({
      async pull(controller) {
        try {
          const { done, value } = await reader.read();
          if (done) {
            finish();
            reader.releaseLock();
            controller.close();
          } else {
            controller.enqueue(value);
          }
        } catch (error) {
          finish();
          reader.releaseLock();
          controller.error(error);
        }
      },
      async cancel(reason) {
        finish();
        try {
          await reader.cancel(reason);
        } finally {
          reader.releaseLock();
        }
      },
    }),
    response,
  );
}

export function isTLPreviewPayload(value: unknown): value is TLPreviewPayload {
  return (
    isRecord(value) &&
    typeof value.type === "string" &&
    EVENT_TYPES.has(value.type as TLPreviewEventType)
  );
}

function copyStore(store: TLPreviewStore): TLPreviewStore {
  return {
    attempts: { ...store.attempts },
    closed: { ...store.closed },
  };
}

/**
 * Consume the ephemeral preview protocol without touching chat messages.
 * Updates are cumulative snapshots, so an older revision is ignored.
 */
export function consumeTLPreviewEvent(
  store: TLPreviewStore,
  value: unknown,
): TLPreviewConsumeResult {
  if (!isTLPreviewPayload(value)) return { handled: false, store };

  const key = eventKey(value);
  // Recognized preview events are always filtered from the SDK Builder, even
  // when malformed. The backend remains authoritative for the final output.
  if (!key) return { handled: true, store };

  if (value.type === "preview_start") {
    if (store.closed[key]) return { handled: true, store };
    const next = copyStore(store);
    next.attempts[key] = {
      runId: value.run_id as string,
      invocationId: value.invocation_id as string,
      attemptId: value.attempt_id as string,
      revision: 0,
      kind: null,
      items: {},
    };
    return { handled: true, store: next };
  }

  if (value.type === "preview_clear") {
    const next = copyStore(store);
    delete next.attempts[key];
    next.closed[key] = true;
    return { handled: true, store: next };
  }

  const current = store.attempts[key];
  const revision = value.revision;
  const itemIndex = value.item_index;
  const kind = value.kind;
  if (
    !current ||
    store.closed[key] ||
    !Number.isInteger(revision) ||
    (revision as number) <= current.revision ||
    !Number.isInteger(itemIndex) ||
    (itemIndex as number) < 0 ||
    (kind !== "final_text" && kind !== "tool_call") ||
    typeof value.text !== "string"
  ) {
    return { handled: true, store };
  }

  const next = copyStore(store);
  const nextAttempt: TLPreviewAttempt = {
    ...current,
    revision: revision as number,
    kind,
    items: { ...current.items, [itemIndex as number]: value.text },
  };
  next.attempts[key] = nextAttempt;
  return { handled: true, store: next };
}

export function previewAttemptEntries(
  store: TLPreviewStore,
): Array<[string, TLPreviewAttempt]> {
  return Object.entries(store.attempts).sort(([, left], [, right]) =>
    left.attemptId.localeCompare(right.attemptId),
  );
}
