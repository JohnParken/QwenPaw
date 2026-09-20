// Status line, run-lock, and chat-mode helpers shared by every feature module.

import { state } from "./state";
import { $, input } from "./dom";

export type ChatMode = "local" | "service";

/** Reads the chat-mode selector and normalizes it to a `ChatMode`. */
export const chatMode = (): ChatMode =>
  $<HTMLSelectElement>("chat-mode").value === "service" ? "service" : "local";

/** Trimmed prompt textarea value. */
export const promptInput = (): string => input("prompt");

/** Sanitizer injected by main.ts so this module stays independent of ApiClient. */
let sanitize: (value: unknown) => string = (message) => String(message);

export function setSanitizer(fn: (value: unknown) => string): void {
  sanitize = fn;
}

/** Writes the connection/status banner shown above the active view. */
export function notice(message: string, error = false): void {
  const banner = $("notice");
  banner.textContent = sanitize(message);
  banner.classList.toggle("error", error);
}

/**
 * Serializes a task behind the page-wide busy lock and reports the outcome.
 *
 * Preserves the legacy semantics exactly:
 * - re-entrant calls are dropped silently (no notice, no queue);
 * - the connection fieldset and (optionally) the work fieldset are disabled;
 * - `agent` is re-disabled in service mode, matching the mode-specific rules;
 * - an empty `success` string suppresses the completion notice.
 */
export async function run(
  task: () => Promise<void>,
  success = "操作完成",
  lockWorkFields = true,
): Promise<void> {
  if (state.busy) return;
  state.busy = true;
  for (const id of ["chat-mode", "chats", "agent"])
    $<HTMLSelectElement>(id).disabled = true;
  if (lockWorkFields) $<HTMLFieldSetElement>("work-fields").disabled = true;
  $<HTMLFieldSetElement>("connection-fields").disabled = true;
  notice("请求处理中…");
  try {
    await task();
    if (success) notice(success);
  } catch (error) {
    notice(error instanceof Error ? error.message : String(error), true);
  } finally {
    state.busy = false;
    for (const id of ["chat-mode", "chats"])
      $<HTMLSelectElement>(id).disabled = false;
    $<HTMLSelectElement>("agent").disabled = chatMode() === "service";
    if (lockWorkFields) $<HTMLFieldSetElement>("work-fields").disabled = false;
    $<HTMLFieldSetElement>("connection-fields").disabled = false;
  }
}

/**
 * Binds a form's `submit` or a button's `click` to a `run()` task.
 *
 * Service approvals live inside the chat fieldset, so a service-mode send keeps
 * that fieldset interactive while a run waits for the user's decision.
 */
export function action(
  id: string,
  task: () => Promise<void>,
  success?: string,
): void {
  const element = $(id);
  element.addEventListener(
    element.tagName === "FORM" ? "submit" : "click",
    (event) => {
      event.preventDefault();
      void run(task, success, id !== "send-form" || chatMode() !== "service");
    },
  );
}
