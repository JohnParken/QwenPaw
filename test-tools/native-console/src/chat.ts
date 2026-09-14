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
export class ChatStream {
  blocks = new Map<string, Map<number, string>>();
  complete = false;
  error = "";
  consume(raw: unknown): void {
    if (raw === "[DONE]") {
      this.complete = true;
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
    if (
      event.object === "response" &&
      ["completed", "failed", "cancelled", "canceled"].includes(event.status)
    ) {
      this.complete = true;
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
    if (event.object === "message" && msg.role === "assistant" && msg.content) {
      const text = textContent(msg.content);
      if (text) this.blocks.set(id, new Map([[0, text]]));
    }
  }
  get text(): string {
    return [...this.blocks.values()]
      .map((blocks) =>
        [...blocks.entries()]
          .sort(([a], [b]) => a - b)
          .map(([, text]) => text)
          .join(""),
      )
      .join("\n\n");
  }
}
