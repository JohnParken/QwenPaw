export type RecordValue = Record<string, any>;

export function textContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content))
    return content.map(textContent).filter(Boolean).join("\n");
  if (content && typeof content === "object") {
    const item = content as RecordValue;
    return typeof item.text === "string"
      ? item.text
      : item.content
        ? textContent(item.content)
        : "";
  }
  return "";
}

export interface ToolCallItem {
  id: string;
  name: string;
  args: string;
  output?: string;
  status: ToolStatus;
}

export type ToolStatus =
  | "preparing"
  | "awaiting_approval"
  | "running"
  | "completed"
  | "failed"
  | "denied"
  | "unknown"
  | "cancelled";

export interface TextTimelineItem {
  key: string;
  kind: "text";
  text: string;
  messageId: string;
  index: number;
}

export interface PreviewTimelineItem {
  key: string;
  kind: "preview";
  text: string;
  previewType: "preview_start" | "preview_update" | "preview_clear";
  status: "active" | "cleared";
  previewKind?: string;
  reason?: string;
}

export interface ToolTimelineItem {
  key: string;
  kind: "tool";
  tool: ToolCallItem;
}

export type TimelineItem =
  | TextTimelineItem
  | PreviewTimelineItem
  | ToolTimelineItem;

export interface TokenUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export interface ContextUsage {
  estimated_tokens?: number;
  max_input_length?: number;
  context_usage_ratio?: number;
}

export interface SubagentItem {
  id: string;
  name: string;
  status: string;
  task?: string;
}

export interface PendingApprovalItem {
  requestId: string;
  sessionId?: string;
  toolCallId?: string;
  toolName: string;
  args?: any;
  reason?: string;
}

const MAX_TOOL_LOG = 65_536;

function boundedLog(value: string): string {
  return value.length > MAX_TOOL_LOG ? value.slice(-MAX_TOOL_LOG) : value;
}

const TOOL_STATUSES = new Set<ToolStatus>([
  "preparing",
  "awaiting_approval",
  "running",
  "completed",
  "failed",
  "denied",
  "unknown",
  "cancelled",
]);

export function normalizeToolStatus(
  value: unknown,
  fallback: ToolStatus = "unknown",
): ToolStatus {
  const normalized = String(value || "");
  if (normalized === "interrupted") return "unknown";
  if (TOOL_STATUSES.has(normalized as ToolStatus))
    return normalized as ToolStatus;
  if (["success", "succeeded", "ok"].includes(normalized)) return "completed";
  if (["error", "errorred"].includes(normalized)) return "failed";
  return fallback;
}

function jsonValue(value: unknown, fallback = "{}"): string {
  if (typeof value === "string") return value;
  if (value === undefined) return fallback;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function toolNameValue(tool: RecordValue): string | undefined {
  const value = tool.name ?? tool.tool_name ?? tool.function?.name;
  return value === undefined || value === null ? undefined : String(value);
}

function toolArgumentsValue(tool: RecordValue): string | undefined {
  const value =
    tool.arguments !== undefined
      ? tool.arguments
      : tool.args !== undefined
        ? tool.args
        : tool.function?.arguments;
  return value === undefined ? undefined : jsonValue(value);
}

function isToolEnvelope(event: RecordValue): boolean {
  const data = event.data;
  return Boolean(
    event.tool_call ||
      event.tool_call_output ||
      event.type === "tool" ||
      event.type === "tool_call" ||
      event.type === "tool_output" ||
      event.type === "tool_call_output" ||
      event.type === "tool_result" ||
      event.object === "tool_call" ||
      event.object === "tool_call_output" ||
      (event.object === "content" &&
        event.type === "data" &&
        data &&
        typeof data === "object" &&
        (data.call_id !== undefined ||
          data.tool_call_id !== undefined ||
          data.output !== undefined ||
          data.result !== undefined)) ||
      (event.object === "message" &&
        ["plugin_call", "plugin_call_output"].includes(event.type)),
  );
}

export class ChatStream {
  blocks = new Map<string, Map<number, string>>();
  complete = false;
  terminalStatus = "";
  error = "";
  explicitThinking = "";
  tools: ToolCallItem[] = [];
  toolMap = new Map<string, ToolCallItem>();
  timeline: TimelineItem[] = [];
  turnUsage: TokenUsage | null = null;
  contextUsage: ContextUsage | null = null;
  subagents: SubagentItem[] = [];
  pendingApproval: PendingApprovalItem | null = null;
  startTime = Date.now();
  thinkingStartTime: number | null = null;
  thinkingEndTime: number | null = null;
  readonly orderedTimeline: boolean;
  private readonly completeOnResponse: boolean;
  private readonly timelineMap = new Map<string, TimelineItem>();
  private readonly seenEventIds = new Set<string>();
  private readonly toolMessageMap = new Map<string, string>();
  private readonly canonicalToolIds = new Set<string>();

  constructor(
    options: { completeOnResponse?: boolean; orderedTimeline?: boolean } = {},
  ) {
    this.completeOnResponse = options.completeOnResponse ?? true;
    this.orderedTimeline = options.orderedTimeline ?? false;
  }

  consume(raw: unknown, eventId?: string): void {
    if (eventId !== undefined) {
      if (this.seenEventIds.has(eventId)) return;
      this.seenEventIds.add(eventId);
    }
    if (raw === "[DONE]") {
      this.markTerminal("completed");
      return;
    }
    if (!raw || typeof raw !== "object") return;
    const event = raw as RecordValue;

    const toolEnvelope = isToolEnvelope(event);
    if (!toolEnvelope && ["cancelled", "canceled"].includes(event.status)) {
      this.error = "后端已取消本轮响应";
    }
    if (
      !toolEnvelope &&
      (event.error ||
        ["failed", "interrupted"].includes(String(event.status))) ||
      event.type === "error"
    ) {
      this.error =
        typeof event.error === "string"
          ? event.error
          : JSON.stringify(event.error || event);
    }

    // Token usage & context usage
    if (event.usage || event.turn_usage || event.type === "turn_usage") {
      const u = event.usage || event.turn_usage || event;
      this.turnUsage = {
        prompt_tokens: u.prompt_tokens ?? u.input_tokens,
        completion_tokens: u.completion_tokens ?? u.output_tokens,
        total_tokens: u.total_tokens,
      };
      if (
        u.context_usage ||
        u.ctx ||
        u.estimated_tokens ||
        u.max_input_length
      ) {
        const c = u.context_usage || u.ctx || u;
        this.contextUsage = {
          estimated_tokens: c.estimated_tokens,
          max_input_length: c.max_input_length,
          context_usage_ratio: c.context_usage_ratio,
        };
      }
    }

    // Subagent tracking
    if (
      event.type === "subagent" ||
      event.subagent ||
      event.object === "subagent" ||
      event.type === "spawn_subagent"
    ) {
      const s = event.subagent || event;
      const id = String(
        s.id || s.subagent_id || `sub_${this.subagents.length + 1}`,
      );
      const existing = this.subagents.find((x) => x.id === id);
      if (existing) {
        existing.status = s.status || existing.status;
        existing.task = s.task || existing.task;
      } else {
        this.subagents.push({
          id,
          name: String(s.name || s.role || "Subagent"),
          status: String(s.status || "running"),
          task: s.task || s.prompt || "",
        });
      }
    }

    // Explicit reasoning / thinking events
    if (
      event.object === "reasoning" ||
      event.type === "reasoning" ||
      typeof event.reasoning_content === "string"
    ) {
      if (!this.thinkingStartTime) {
        this.thinkingStartTime = Date.now();
      }
      const rText =
        typeof event.reasoning_content === "string"
          ? event.reasoning_content
          : typeof event.text === "string"
            ? event.text
            : textContent(event.content);
      if (rText) {
        this.explicitThinking += rText;
      }
    }

    // Normalized service tool records use `type: tool`. The legacy runtime
    // tool_call envelope remains supported for the local management console.
    const toolCall =
      event.type === "tool" && !event.tool_call
        ? event
        : event.tool_call ||
          (event.object === "tool_call" || event.type === "tool_call"
            ? event
            : null);
    if (toolCall) {
      const id = String(
        toolCall.tool_call_id ||
          toolCall.id ||
          toolCall.call_id ||
          `tool_${this.tools.length + 1}`,
      );
      const name = toolNameValue(toolCall);
      const args = toolArgumentsValue(toolCall);
      let existing = this.toolMap.get(id);
      if (!existing) {
        existing = {
          id,
          name: name || "tool",
          args: args ?? "{}",
          status: normalizeToolStatus(
            toolCall.status,
            event.type === "tool" ? "preparing" : "running",
          ),
        };
        this.toolMap.set(id, existing);
        this.tools.push(existing);
        const item: ToolTimelineItem = {
          key: `tool:${id}`,
          kind: "tool",
          tool: existing,
        };
        this.timelineMap.set(item.key, item);
        this.timeline.push(item);
      } else {
        if (name !== undefined) existing.name = name;
        if (args !== undefined) existing.args = args;
        if (toolCall.status !== undefined)
          existing.status = normalizeToolStatus(toolCall.status, existing.status);
      }
      if (event.type === "tool") this.canonicalToolIds.add(id);
      const directOutput =
        toolCall.log !== undefined ? toolCall.log : toolCall.output;
      if (directOutput !== undefined)
        existing.output = boundedLog(jsonValue(directOutput, ""));
    }

    // Tool call output / result
    const toolOutput =
      (event.type === "tool_output" && !event.tool_call_output
        ? event
        : event.tool_call_output) ||
      (event.object === "tool_call_output" ||
      event.type === "tool_call_output" ||
      event.type === "tool_result"
        ? event
        : null);
    if (toolOutput) {
      const id = String(
        toolOutput.id || toolOutput.tool_call_id || toolOutput.call_id || "",
      );
      const outputValue =
        toolOutput.text !== undefined
          ? toolOutput.text
          : toolOutput.output !== undefined
            ? toolOutput.output
            : toolOutput.result !== undefined
              ? toolOutput.result
              : toolOutput.error !== undefined
                ? toolOutput.error
                : "";
      const output = boundedLog(jsonValue(outputValue, ""));
      const item = id
        ? this.toolMap.get(id)
        : this.tools[this.tools.length - 1];
      if (item) {
        item.output =
          event.type === "tool_output"
            ? boundedLog((item.output || "") + output)
            : output;
        if (toolOutput.status !== undefined)
          item.status = normalizeToolStatus(toolOutput.status, item.status);
        else if (toolOutput.error) item.status = "failed";
        else if (toolOutput.done === true) item.status = "completed";
        else if (event.type !== "tool_output") item.status = "completed";
      } else {
        const newItem: ToolCallItem = {
          id: id || `tool_${this.tools.length + 1}`,
          name: String(toolOutput.tool_name || "tool_result"),
          args: "",
          output,
          status: normalizeToolStatus(
            toolOutput.status,
            toolOutput.error
              ? "failed"
              : event.type === "tool_output"
                ? "running"
                : "completed",
          ),
        };
        this.tools.push(newItem);
        this.toolMap.set(newItem.id, newItem);
        const timelineItem: ToolTimelineItem = {
          key: `tool:${newItem.id}`,
          kind: "tool",
          tool: newItem,
        };
        this.timelineMap.set(timelineItem.key, timelineItem);
        this.timeline.push(timelineItem);
      }
    }

    // Approval records may be nested (`approval`) or use the legacy
    // `approval_request` shape. A decided record has no pending card, and
    // the service uses `call_id` inside the nested approval object.
    const approval =
      event.approval ||
      event.approval_request ||
      (["approval", "approval_request", "approval_decided", "approval_decision"].includes(
        event.type,
      )
        ? event
        : null);
    if (approval && typeof approval === "object") {
      const req = {
        ...event,
        ...(approval as RecordValue),
      } as RecordValue;
      const toolCallIdValue =
        req.tool_call_id ?? req.call_id ?? event.tool_call_id ?? event.call_id;
      const toolCallId =
        toolCallIdValue === undefined || toolCallIdValue === null
          ? undefined
          : String(toolCallIdValue);
      const toolName = String(req.tool_name ?? req.name ?? "unknown_tool");
      const toolArgs = req.arguments ?? req.args;
      const requestId = String(
        req.request_id ?? req.approval_id ?? req.id ?? "",
      );
      const approvalStatus = String(req.status ?? event.status ?? "pending").toLowerCase();
      const decided =
        [
          "approved",
          "denied",
          "rejected",
          "expired",
          "cancelled",
          "canceled",
          "decided",
        ].includes(approvalStatus) ||
        (req.approved !== undefined && req.approved !== null) ||
        event.type === "approval_decided" ||
        event.type === "approval_decision";

      if (toolCallId && !this.toolMap.has(toolCallId) && !decided) {
        this.consume({
          type: "tool",
          tool_call_id: toolCallId,
          name: toolName,
          arguments: toolArgs ?? {},
          status: "awaiting_approval",
        });
      } else if (toolCallId) {
        const item = this.toolMap.get(toolCallId);
        if (item) {
          if (!decided) item.status = "awaiting_approval";
          else if (approvalStatus === "approved" || req.approved === true)
            item.status = "running";
          else if (["denied", "rejected"].includes(approvalStatus) || req.approved === false)
            item.status = "denied";
          else if (["expired", "cancelled", "canceled"].includes(approvalStatus))
            item.status = "cancelled";
        }
      }

      if (decided) {
        this.pendingApproval = null;
      } else {
        this.pendingApproval = {
          requestId,
          sessionId: req.session_id,
          toolCallId,
          toolName,
          args: toolArgs,
          reason:
            req.reason ??
            req.reasoning ??
            req.result_summary ??
            "工具执行需要审批",
        };
      }
    }

    if (event.type === "preview_start" || event.type === "preview_update" || event.type === "preview_clear") {
      this.consumePreview(event);
    }

    if (event.type === "terminal" || event.event === "end") {
      this.markTerminal(String(event.status || "completed"));
      return;
    }

    // Response completion
    if (
      event.object === "response" &&
      ["completed", "failed", "cancelled", "canceled"].includes(event.status)
    ) {
      this.consumeFinalOutput(event.output);
      if (this.completeOnResponse) this.markTerminal(String(event.status));
    }

    const msg = event.message || event;
    const id = String(event.msg_id || event.id || msg.id || "assistant");
    if (
      event.object === "content" &&
      event.type === "data" &&
      event.data &&
      typeof event.data === "object"
    ) {
      this.consumeDataContent(event, event.data as RecordValue, id);
    } else if (event.object === "content" && typeof event.text === "string") {
      const blocks = this.blocks.get(id) || new Map<number, string>();
      const index = Number(event.index || 0);
      blocks.set(
        index,
        event.delta === true
          ? (blocks.get(index) || "") + event.text
          : event.text,
      );
      this.blocks.set(id, blocks);
      this.upsertTextTimeline(id, index, blocks.get(index) || "");
    } else if (
      (event.type === "text" || event.type === "text_delta") &&
      typeof event.text === "string"
    ) {
      const blocks = this.blocks.get(id) || new Map<number, string>();
      const index = Number(event.index || 0);
      blocks.set(index, event.delta === false ? event.text : (blocks.get(index) || "") + event.text);
      this.blocks.set(id, blocks);
      this.upsertTextTimeline(id, index, blocks.get(index) || "");
    }

    if (
      msg.content &&
      (msg.role === "assistant" ||
        msg.role === "tool" ||
        ["plugin_call", "plugin_call_output"].includes(msg.type))
    ) {
      this.consumeMessageSnapshot(msg, id);
    }
  }

  markTerminal(status = "completed"): void {
    this.complete = true;
    this.terminalStatus = status;
    if (
      ["failed", "cancelled", "canceled", "interrupted"].includes(status) &&
      !this.error
    )
      this.error = status === "failed" ? "服务运行失败" : "后端已取消本轮响应";
    if (this.thinkingStartTime && !this.thinkingEndTime)
      this.thinkingEndTime = Date.now();
    if (["cancelled", "canceled", "failed", "interrupted"].includes(status)) {
      for (const item of this.tools) {
        if (["preparing", "awaiting_approval", "running"].includes(item.status))
          item.status = ["failed", "interrupted"].includes(status)
            ? "unknown"
            : "cancelled";
      }
    }
    this.removePreviewItems();
    if (this.pendingApproval && status !== "waiting_approval")
      this.pendingApproval = null;
  }

  private upsertTextTimeline(messageId: string, index: number, text: string): void {
    const key = `text:${messageId}:${index}`;
    const existing = this.timelineMap.get(key) as TextTimelineItem | undefined;
    if (existing && existing.kind === "text") {
      existing.text = text;
      return;
    }
    const item: TextTimelineItem = { key, kind: "text", text, messageId, index };
    this.timelineMap.set(key, item);
    this.timeline.push(item);
  }

  private replaceMessageText(
    messageId: string,
    blocks: Map<number, string>,
  ): void {
    for (const [key, item] of this.timelineMap) {
      if (
        item.kind === "text" &&
        item.messageId === messageId &&
        !blocks.has(item.index)
      ) {
        this.timelineMap.delete(key);
      }
    }
    this.timeline = this.timeline.filter(
      (item) =>
        item.kind !== "text" ||
        item.messageId !== messageId ||
        blocks.has(item.index),
    );
    this.blocks.set(messageId, blocks);
    for (const [index, text] of blocks)
      this.upsertTextTimeline(messageId, index, text);
  }

  private consumeDataContent(
    event: RecordValue,
    data: RecordValue,
    messageId: string,
    role?: unknown,
    messageType?: unknown,
  ): void {
    const mappedId = messageId ? this.toolMessageMap.get(messageId) : undefined;
    const idValue = data.call_id ?? data.tool_call_id ?? data.id ?? mappedId;
    const id = idValue === undefined || idValue === null ? "" : String(idValue);
    if (id && messageId) this.toolMessageMap.set(messageId, id);

    const isOutput =
      data.output !== undefined ||
      data.result !== undefined ||
      role === "tool" ||
      String(messageType || "").includes("output");
    if (isOutput) {
      const item = id ? this.toolMap.get(id) : this.tools[this.tools.length - 1];
      if (!item) return;
      const value =
        data.output !== undefined ? data.output : data.result;
      if (value !== undefined)
        item.output = boundedLog(jsonValue(value, ""));
      if (!this.canonicalToolIds.has(item.id)) item.status = "completed";
      return;
    }

    if (!id) return;
    let item = this.toolMap.get(id);
    if (!item) {
      this.consume({
        type: "tool_call",
        id,
        name: toolNameValue(data) || "tool",
        arguments: "",
        status: "preparing",
      });
      item = this.toolMap.get(id);
    }
    if (!item) return;
    const canonical = this.canonicalToolIds.has(id);
    const name = toolNameValue(data);
    if (!canonical && name !== undefined) item.name = name;

    const args = data.arguments ?? data.args;
    if (args !== undefined && !canonical) {
      const serialized = jsonValue(args, "");
      item.args = event.delta === true ? item.args + serialized : serialized;
    }
    if (!canonical && item.status === "completed") item.status = "preparing";
  }

  private consumeMessageSnapshot(msg: RecordValue, messageId: string): void {
    const content = msg.content;
    if (!Array.isArray(content)) {
      const text = textContent(content);
      if (text) this.replaceMessageText(messageId, new Map([[0, text]]));
      return;
    }

    const blocks = new Map<number, string>();
    let hasText = false;
    content.forEach((item: unknown, position: number) => {
      if (item && typeof item === "object") {
        const value = item as RecordValue;
        if (value.type === "data" && value.data && typeof value.data === "object") {
          this.consumeDataContent(
            {
              object: "content",
              type: "data",
              data: value.data,
              delta: value.delta,
              msg_id: messageId,
            },
            value.data as RecordValue,
            messageId,
            msg.role,
            msg.type,
          );
          return;
        }
      }
      const text = textContent(item);
      if (text) {
        hasText = true;
        const index = Number(
          item && typeof item === "object" && (item as RecordValue).index !== undefined
            ? (item as RecordValue).index
            : position,
        );
        blocks.set(index, text);
      }
    });
    if (hasText) this.replaceMessageText(messageId, blocks);
  }

  private consumeFinalOutput(output: unknown): void {
    if (!Array.isArray(output)) return;
    for (const item of output as RecordValue[]) {
      if (item.role === "assistant" && textContent(item.content)) {
        const id = String(item.id || "assistant");
        this.consumeMessageSnapshot(item, id);
      }
      const calls = item.type === "tool_call" || item.tool_calls
        ? Array.isArray(item.tool_calls) ? item.tool_calls : [item]
        : [];
      for (const call of calls) {
        const id = String(call.tool_call_id || call.call_id || call.id || "");
        const existing = id ? this.toolMap.get(id) : undefined;
        if (existing && !this.completeOnResponse) {
          // Service history contains both runtime envelopes and the canonical
          // normalized tool item. Keep the canonical dispatch status.
          if (
            !this.canonicalToolIds.has(id) &&
            (call.arguments !== undefined || call.args !== undefined)
          )
            existing.args = jsonValue(call.arguments ?? call.args);
          continue;
        }
        this.consume({
          type: "tool",
          ...call,
          status: call.status || (this.completeOnResponse ? "completed" : "unknown"),
        });
      }
    }
  }

  private consumePreview(event: RecordValue): void {
    const type = event.type;
    if (!["preview_start", "preview_update", "preview_clear"].includes(type)) return;
    const attemptKey = this.previewAttemptKey(event);
    if (type === "preview_clear") {
      this.removePreviewItems(attemptKey);
      return;
    }
    const key = `${attemptKey}:${event.item_index ?? 0}`;
    let item = this.timelineMap.get(key) as PreviewTimelineItem | undefined;
    if (!item || item.kind !== "preview") {
      item = {
        key,
        kind: "preview",
        text: "",
        previewType: type,
        status: "active",
      };
      this.timelineMap.set(key, item);
      this.timeline.push(item);
    }
    item.previewType = type;
    item.previewKind = event.kind;
    item.reason = event.reason;
    if (type === "preview_update") item.text = typeof event.text === "string" ? event.text : "";
  }

  private previewAttemptKey(event: RecordValue): string {
    return `preview:${event.run_id || ""}:${event.invocation_id || ""}:${event.attempt_id || ""}`;
  }

  private removePreviewItems(attemptKey?: string): void {
    const matches = (item: TimelineItem): boolean =>
      item.kind === "preview" &&
      (attemptKey === undefined || item.key.startsWith(`${attemptKey}:`));
    for (const [key, item] of this.timelineMap) {
      if (matches(item)) this.timelineMap.delete(key);
    }
    this.timeline = this.timeline.filter((item) => !matches(item));
  }

  get rawText(): string {
    return [...this.blocks.values()]
      .map((blocks) =>
        [...blocks.entries()]
          .sort(([a], [b]) => a - b)
          .map(([, text]) => text)
          .join(""),
      )
      .join("\n\n");
  }

  get thinking(): string {
    if (this.explicitThinking) return this.explicitThinking.trim();
    const raw = this.rawText;
    const thinkStart = raw.indexOf("<think>");
    if (thinkStart !== -1) {
      const thinkEnd = raw.indexOf("</think>");
      if (thinkEnd !== -1) {
        return raw.slice(thinkStart + 7, thinkEnd).trim();
      } else {
        return raw.slice(thinkStart + 7).trim();
      }
    }
    return "";
  }

  get isThinking(): boolean {
    if (this.complete) return false;
    if (this.explicitThinking.length > 0 && !this.text) return true;
    const raw = this.rawText;
    return raw.includes("<think>") && !raw.includes("</think>");
  }

  get thinkingDurationSeconds(): number {
    const start = this.thinkingStartTime;
    if (!start) return 0;
    const end = this.thinkingEndTime || Date.now();
    return Math.max(0, Math.round((end - start) / 100) / 10);
  }

  get text(): string {
    const raw = this.rawText;
    const thinkStart = raw.indexOf("<think>");
    if (thinkStart !== -1) {
      const thinkEnd = raw.indexOf("</think>");
      if (thinkEnd !== -1) {
        const before = raw.slice(0, thinkStart);
        const after = raw.slice(thinkEnd + 8);
        return (before + after).trim();
      } else {
        // still inside think tag
        return raw.slice(0, thinkStart).trim();
      }
    }
    return raw;
  }
}
