export class SseParser {
  constructor(max = 1048576) { this.buffer = ""; this.max = max; this.pendingCR = false; }
  push(chunk) {
    if (this.pendingCR && chunk.startsWith("\n")) chunk = chunk.slice(1);
    this.pendingCR = chunk.endsWith("\r");
    this.buffer += chunk.replace(/\r\n|\r/g, "\n");
    const events = [];
    let split;
    while ((split = this.buffer.indexOf("\n\n")) >= 0) {
      if (split > this.max) throw new Error("SSE event limit exceeded");
      const block = this.buffer.slice(0, split);
      this.buffer = this.buffer.slice(split + 2);
      const event = {event: "message", data: "", id: undefined};
      const data = [];
      for (const line of block.split("\n")) {
        if (line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const key = colon < 0 ? line : line.slice(0, colon);
        let value = colon < 0 ? "" : line.slice(colon + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (key === "data") data.push(value);
        if (key === "event") event.event = value;
        if (key === "id" && !value.includes("\0")) event.id = value;
      }
      if (data.length) { event.data = data.join("\n"); events.push(event); }
    }
    if (this.buffer.length > this.max) throw new Error("SSE buffer limit exceeded");
    return events;
  }
}
