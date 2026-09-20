// Shared DOM helpers. Every view module goes through these so that the
// element-id contract used by the unit and browser tests stays in one place.

export const $ = <T extends HTMLElement = HTMLElement>(id: string): T =>
  document.getElementById(id) as T;

/** Trimmed value of an input/select/textarea by id. */
export const input = (id: string): string =>
  $<HTMLInputElement>(id).value.trim();

/** Raw value of an input/select/textarea by id. */
export const value = (id: string): string => $<HTMLInputElement>(id).value;

export const json = (data: unknown): string => JSON.stringify(data, null, 2);

/** Builds an `/api` URL with URL-encoded query parameters. */
export const query = (
  path: string,
  params: Record<string, string | number>,
): string =>
  "/api" +
  path +
  "?" +
  new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));

export function node<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text = "",
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  el.textContent = text;
  return el;
}

/** Replaces a single-select's options, keeping `selected` when present. */
export function selectOptions(
  id: string,
  items: { value: string; label: string }[],
  selected = "",
): void {
  const select = $<HTMLSelectElement>(id);
  select.replaceChildren(
    ...items.map((item) => {
      const option = node("option", item.label);
      option.value = item.value;
      return option;
    }),
  );
  if (items.some((item) => item.value === selected)) select.value = selected;
}

export function download(data: Blob, name: string): void {
  const url = URL.createObjectURL(data);
  const anchor = node("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Coalesces repeated calls inside one animation frame. Used by the log views so
 * a burst of SSE events triggers a single DOM rebuild per frame.
 */
export function createFrameScheduler(task: () => void): () => void {
  let frame = 0;
  return () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      task();
    });
  };
}

/** Remembers which `<details>` elements were open across a list rebuild. */
export function openDetailIds(list: HTMLElement): Set<string> {
  return new Set(
    [...list.querySelectorAll<HTMLDetailsElement>("details[open]")].map(
      (element) => element.dataset.id || "",
    ),
  );
}
