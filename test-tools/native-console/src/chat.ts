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
  status: "running" | "completed" | "failed";
}

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
  toolName: string;
  args?: any;
  reason?: string;
}

export class ChatStream {
  blocks = new Map<string, Map<number, string>>();
  complete = false;
  error = "";
  explicitThinking = "";
  tools: ToolCallItem[] = [];
  toolMap = new Map<string, ToolCallItem>();
  turnUsage: TokenUsage | null = null;
  contextUsage: ContextUsage | null = null;
  subagents: SubagentItem[] = [];
  pendingApproval: PendingApprovalItem | null = null;
  startTime = Date.now();
  thinkingStartTime: number | null = null;
  thinkingEndTime: number | null = null;

  consume(raw: unknown): void {
    if (raw === "[DONE]") {
      this.complete = true;
      if (this.thinkingStartTime && !this.thinkingEndTime) {
        this.thinkingEndTime = Date.now();
      }
      return;
    }
    if (!raw || typeof raw !== "object") return;
    const event = raw as RecordValue;

    if (["cancelled", "canceled"].includes(event.status)) {
      this.error = "后端已取消本轮响应";
    }
    if (event.error || event.status === "failed" || event.type === "error") {
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

    // Tool call start
    const toolCall =
      event.tool_call ||
      (event.object === "tool_call" || event.type === "tool_call"
        ? event
        : null);
    if (toolCall) {
      const id = String(
        toolCall.id || toolCall.tool_call_id || `tool_${this.tools.length + 1}`,
      );
      const name = String(
        toolCall.name ||
          toolCall.tool_name ||
          toolCall.function?.name ||
          "tool",
      );
      const args =
        typeof toolCall.arguments === "string"
          ? toolCall.arguments
          : JSON.stringify(toolCall.arguments || toolCall.args || {}, null, 2);
      let existing = this.toolMap.get(id);
      if (!existing) {
        existing = { id, name, args, status: "running" };
        this.toolMap.set(id, existing);
        this.tools.push(existing);
      } else {
        existing.name = name;
        existing.args = args;
      }
    }

    // Tool call output / result
    const toolOutput =
      event.tool_call_output ||
      (event.object === "tool_call_output" ||
      event.type === "tool_call_output" ||
      event.type === "tool_result"
        ? event
        : null);
    if (toolOutput) {
      const id = String(toolOutput.id || toolOutput.tool_call_id || "");
      const output =
        typeof toolOutput.output === "string"
          ? toolOutput.output
          : JSON.stringify(
              toolOutput.output ?? toolOutput.result ?? "",
              null,
              2,
            );
      const item = id
        ? this.toolMap.get(id)
        : this.tools[this.tools.length - 1];
      if (item) {
        item.output = output;
        item.status =
          toolOutput.status === "failed" || toolOutput.error
            ? "failed"
            : "completed";
      } else {
        const newItem: ToolCallItem = {
          id: id || `tool_${this.tools.length + 1}`,
          name: String(toolOutput.tool_name || "tool_result"),
          args: "",
          output,
          status:
            toolOutput.status === "failed" || toolOutput.error
              ? "failed"
              : "completed",
        };
        this.tools.push(newItem);
      }
    }

    // Approval request event
    if (
      event.type === "approval_request" ||
      event.object === "approval_request" ||
      event.approval_request
    ) {
      const req = event.approval_request || event;
      this.pendingApproval = {
        requestId: String(req.request_id || req.id || ""),
        sessionId: req.session_id,
        toolName: String(req.tool_name || req.name || "unknown_tool"),
        args: req.arguments || req.args,
        reason: req.reason || "工具执行需要审批",
      };
    }

    // Response completion
    if (
      event.object === "response" &&
      ["completed", "failed", "cancelled", "canceled"].includes(event.status)
    ) {
      this.complete = true;
      if (this.thinkingStartTime && !this.thinkingEndTime) {
        this.thinkingEndTime = Date.now();
      }
      const output = Array.isArray(event.output) ? event.output : [];
      const messages = output.filter(
        (item: RecordValue) =>
          item.role === "assistant" && textContent(item.content),
      );
      if (messages.length) {
        this.blocks.clear();
        messages.forEach((item: RecordValue, index: number) =>
          this.blocks.set(
            String(item.id || index),
            new Map([[0, textContent(item.content)]]),
          ),
        );
      }

      // Check for tools in completed response output
      for (const item of output) {
        if (item.type === "tool_call" || item.tool_calls) {
          const calls = Array.isArray(item.tool_calls)
            ? item.tool_calls
            : [item];
          for (const c of calls) {
            const id = String(c.id || `tool_${this.tools.length + 1}`);
            if (!this.toolMap.has(id)) {
              const name = String(c.name || c.function?.name || "tool");
              const args =
                typeof c.arguments === "string"
                  ? c.arguments
                  : JSON.stringify(c.arguments || {}, null, 2);
              const toolItem: ToolCallItem = {
                id,
                name,
                args,
                status: "completed",
              };
              this.toolMap.set(id, toolItem);
              this.tools.push(toolItem);
            }
          }
        }
      }
    }

    const id = String(event.msg_id || event.id || "assistant");
    if (event.object === "content" && typeof event.text === "string") {
      const blocks = this.blocks.get(id) || new Map<number, string>();
      const index = Number(event.index || 0);
      blocks.set(
        index,
        event.delta === true
          ? (blocks.get(index) || "") + event.text
          : event.text,
      );
      this.blocks.set(id, blocks);
    }

    const msg = event.message || event;
    if (msg.role === "assistant" && msg.content) {
      const text = textContent(msg.content);
      if (text) this.blocks.set(id, new Map([[0, text]]));
    }
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
