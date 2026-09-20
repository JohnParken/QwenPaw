// Protocol (HTTP/SSE) request log and model-diagnostic drawers.
//
// Both drawers share the same shape: a filterable list of `<details>` rows whose
// open/closed state survives a rebuild, plus export/clear controls.

import type { ApiClient } from "../api";
import {
  isModelLogEvent,
  ModelLogStore,
  type ModelLogMode,
} from "../model-log";
import {
  $,
  createFrameScheduler,
  download,
  input,
  json,
  node,
  openDetailIds,
} from "../core/dom";

/** Reads the Debug checkbox that gates verbose transport logging. */
export const modelDebugEnabled = (): boolean =>
  $<HTMLInputElement>("debug-toggle").checked === true;

export interface LogPanels {
  renderModelLogs(): void;
  clearModelLogs(): void;
  /**
   * Routes a streamed event into the diagnostic store when it is a recognized
   * `model_log` payload. Returns true when consumed, so callers can keep
   * diagnostics out of the chat renderer.
   */
  captureModelLog(
    value: unknown,
    mode: ModelLogMode,
    eventName?: string,
  ): boolean;
}

/**
 * Wires both drawers and returns the model-log controls other modules need
 * (the chat stream pipes recognized diagnostics into the store).
 */
export function bindLogPanels(
  api: ApiClient,
  modelLogs: ModelLogStore,
): LogPanels {
  // --- protocol log -------------------------------------------------------

  function renderLogs(): void {
    const filter = input("log-filter").toLowerCase();
    const list = $("log-list");
    const opened = openDetailIds(list);
    list.replaceChildren();
    $("log-count").textContent = String(api.logs.length);
    for (const log of api.logs) {
      if (
        filter &&
        !`${log.method} ${log.url} ${log.status}`.toLowerCase().includes(filter)
      )
        continue;
      const details = node("details");
      details.dataset.id = String(log.id);
      details.open = opened.has(String(log.id));
      const summary = node(
        "summary",
        `${log.method} ${log.url} · ${log.status} · ${log.duration} ms${log.eventCount ? ` · ${log.eventCount} 事件` : ""}`,
      );
      const pre = node("pre", json(log));
      details.append(summary, pre);
      list.append(details);
    }
  }

  const scheduleLogRender = createFrameScheduler(renderLogs);
  api.onLog = scheduleLogRender;

  // --- model diagnostics --------------------------------------------------

  function renderModelLogs(): void {
    const list = $("model-log-list");
    const filter = input("model-log-filter");
    const opened = openDetailIds(list);
    list.replaceChildren();
    $("model-log-count").textContent = String(modelLogs.size);
    const visible = modelLogs.entries.filter((entry) =>
      modelLogs.matches(entry, filter),
    );
    $("model-log-empty").textContent = modelLogs.size
      ? visible.length
        ? "模型诊断来自后端 model_log 事件；敏感字段已脱敏。"
        : "当前筛选没有匹配的模型诊断事件。"
      : modelDebugEnabled()
        ? "暂无模型诊断事件。后端可能未启用 QWENPAW_MODEL_DEBUG=1，或本轮尚未产生诊断。"
        : "暂无模型诊断事件。开启 Debug 后发送一条消息；后端还需 QWENPAW_MODEL_DEBUG=1。";
    for (const entry of visible) {
      const details = node("details");
      details.dataset.id = entry.id;
      details.open = opened.has(entry.id);
      const summary = node(
        "summary",
        `${entry.mode} · ${entry.event} · ${entry.level}${entry.truncated ? " · 已截断" : ""}`,
      );
      const pre = node("pre");
      pre.textContent = JSON.stringify(entry, null, 2) || String(entry);
      details.append(summary, pre);
      list.append(details);
    }
  }

  const scheduleModelLogRender = createFrameScheduler(renderModelLogs);

  function clearModelLogs(): void {
    modelLogs.clear();
    renderModelLogs();
  }

  function captureModelLog(
    value: unknown,
    mode: ModelLogMode,
    eventName = "",
  ): boolean {
    let candidate = value;
    if (!isModelLogEvent(candidate) && eventName === "model_log") {
      candidate = {
        type: "model_log",
        object: "diagnostic",
        event: eventName,
        level: "info",
        payload: value,
      };
    }
    if (!isModelLogEvent(candidate)) return false;
    const added = modelLogs.add(candidate, mode, (payload) =>
      api.safe(payload),
    );
    if (added) scheduleModelLogRender();
    // Recognized diagnostic replays must never enter the chat renderer.
    return true;
  }

  // --- controls -----------------------------------------------------------

  $("toggle-logs-btn").addEventListener("click", () => {
    const drawer = $("logs-drawer");
    drawer.classList.toggle("closed");
    if (!drawer.classList.contains("closed"))
      $("model-logs-drawer").classList.add("closed");
  });
  $("close-logs-btn").addEventListener("click", () => {
    $("logs-drawer").classList.add("closed");
  });
  $("toggle-model-logs-btn").addEventListener("click", () => {
    const drawer = $("model-logs-drawer");
    drawer.classList.toggle("closed");
    if (!drawer.classList.contains("closed"))
      $("logs-drawer").classList.add("closed");
    renderModelLogs();
  });
  $("close-model-logs-btn").addEventListener("click", () => {
    $("model-logs-drawer").classList.add("closed");
  });
  $("debug-toggle").addEventListener("change", () => {
    api.setVerboseLogging(modelDebugEnabled());
    renderModelLogs();
  });

  $("log-filter").addEventListener("input", renderLogs);
  $("clear-logs").addEventListener("click", () => api.clear());
  $("export-logs").addEventListener("click", () =>
    download(
      new Blob([json(api.logs)], { type: "application/json" }),
      "native-console-logs.json",
    ),
  );

  $("model-log-filter").addEventListener("input", renderModelLogs);
  $("clear-model-logs").addEventListener("click", clearModelLogs);
  $("export-model-logs").addEventListener("click", () =>
    download(
      new Blob([JSON.stringify(modelLogs.entries, null, 2)], {
        type: "application/json",
      }),
      "native-console-model-logs.json",
    ),
  );

  renderModelLogs();

  return { renderModelLogs, clearModelLogs, captureModelLog };
}
