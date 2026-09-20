import { describe, expect, it } from "vitest";
import { renderToolCard } from "../src/tools-render";
import type { ToolCallItem } from "../src/chat";

const tool: ToolCallItem = {
  id: "call",
  name: "shell",
  args: '{"command":"echo hello"}',
  status: "running",
};

describe("tool presentation", () => {
  it("preserves collapsed state while logs and status change", () => {
    const card = renderToolCard(tool);
    card.querySelector("details")!.open = false;
    const updated = renderToolCard(
      { ...tool, status: "completed", output: "hello" },
      card,
    );
    expect(updated).toBe(card);
    expect(updated.querySelector("details")!.open).toBe(false);
    expect(updated.textContent).toContain("hello");
    expect(updated.textContent).toContain("成功");
  });

  it("renders an owned file reference as an authorization action, not a raw storage URL", () => {
    const card = renderToolCard({
      ...tool,
      name: "publish_file",
      status: "completed",
      output: JSON.stringify({
        file: { id: "file-id", name: "report.txt", key: "private/key" },
      }),
    });
    expect(
      card.querySelector<HTMLButtonElement>("button[data-file-id]")?.dataset
        .fileId,
    ).toBe("file-id");
    expect(card.querySelector("a")).toBeNull();
  });

  it("does not execute browser arguments or attribute injection", () => {
    const card = renderToolCard({
      ...tool,
      name: "browser",
      args: JSON.stringify({ url: "javascript:alert(1)" }),
      output: 'https://example.com/a.png" onerror="alert(1)',
    });
    expect(card.querySelector("a")).toBeNull();
    expect(card.querySelector("[onerror]")).toBeNull();
  });
});
