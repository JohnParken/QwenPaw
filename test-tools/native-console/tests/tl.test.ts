// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { TL_FIELDS, validateTL } from "../src/tl";
import { ApiClient } from "../src/api";

const defaults = () =>
  Object.fromEntries(TL_FIELDS.map(([key, , fallback]) => [key, fallback]));
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("preserves empty protocol metadata and only serializes approved TL fields", () => {
  const data = validateTL("http://127.0.0.1:8089", {
    ...defaults(),
    model: "not-a-wire-routing-field",
  });
  expect(data).toMatchObject({
    app_id: "",
    tr_code: "",
    tr_version: "",
    tool_calling_mode: "system_prompt",
    timeout_seconds: 150,
    stream_idle_timeout_seconds: 0,
  });
  expect(data).not.toHaveProperty("model");
});

it.each([
  ["timeout_seconds", 0],
  ["timeout_seconds", Infinity],
  ["stream_idle_timeout_seconds", -1],
  ["json_correction_max_attempts", 2],
  ["json_correction_max_attempts", true],
  ["max_request_bytes", 0.5],
  ["max_response_bytes", ""],
  ["system_prompt_variable_name", " "],
])("rejects invalid TL field %s=%s", (key, value) => {
  expect(() =>
    validateTL("http://127.0.0.1:8089", { ...defaults(), [key]: value }),
  ).toThrow();
});

it.each([
  "ftp://host",
  "http://user:password@host",
  "http://host?token=secret",
  "http://host#fragment",
])("rejects unsupported endpoint %s", (url) => {
  expect(() => validateTL(url, defaults())).toThrow();
});

it("allows a TL stream with idle timeout zero to wait past the generic 60 seconds", async () => {
  vi.useFakeTimers();
  let finish!: (response: Response) => void;
  let signal!: AbortSignal;
  vi.stubGlobal(
    "fetch",
    vi.fn((_path, init) => {
      signal = init.signal;
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    }),
  );
  const api = new ApiClient();
  const pending = api.request("/api/console/chat", {
    timeoutMs: 0,
    onEvent: () => {},
  });
  await vi.advanceTimersByTimeAsync(150000);
  expect(signal.aborted).toBe(false);
  finish(
    new Response('data: {"object":"response","status":"completed"}\n\n', {
      headers: { "content-type": "text/event-stream" },
    }),
  );
  await pending;
  expect(api.logs[0].eventCount).toBe(1);
});
