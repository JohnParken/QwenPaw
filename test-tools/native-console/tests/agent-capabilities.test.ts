import { describe, expect, it } from "vitest";
import { renderMarkdownToHtml, escapeHtml } from "../src/markdown";
import { renderToolCard } from "../src/tools-render";
import { ChatStream } from "../src/chat";
import { ContextMonitor } from "../src/context-monitor";

describe("Markdown Rendering & Security", () => {
  it("escapes dangerous HTML tags against XSS", () => {
    const input =
      '<script>alert("xss")</script><img src="x" onerror="alert(1)">';
    const html = renderMarkdownToHtml(input);
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  it("renders fenced code block with language and copy button", () => {
    const md = "```typescript\nconst a: number = 42;\nconsole.log(a);\n```";
    const html = renderMarkdownToHtml(md);
    expect(html).toContain('class="code-block-container"');
    expect(html).toContain('data-lang="typescript"');
    expect(html).toContain("code-block-copy-btn");
    expect(html).toContain("const a: number = 42;");
  });

  it("renders typography, inline code, bold, links and tables", () => {
    const md = `
# Title

This is **bold** text and \`inline code\`.
[link](https://example.com)

| Name | Role |
| --- | --- |
| QwenPaw | Agent |
`;
    const html = renderMarkdownToHtml(md);
    expect(html).toContain('<h1 class="md-h1">Title</h1>');
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain('<code class="md-inline-code">inline code</code>');
    expect(html).toContain('<table class="md-table">');
    expect(html).toContain("<th>Name</th>");
    expect(html).toContain("<td>QwenPaw</td>");
  });
});

describe("Specialized Tool Card Renderers", () => {
  it("renders Shell command card with prompt and output", () => {
    const card = renderToolCard({
      id: "tool-1",
      name: "shell",
      args: JSON.stringify({ command: "ls -la", cwd: "/workspace" }),
      output: "total 0\n-rw-r--r-- 1 user staff 0 Sep 15 12:00 note.txt",
      status: "completed",
    });

    expect(card.innerHTML).toContain("终端命令 Shell");
    expect(card.innerHTML).toContain("ls -la");
    expect(card.innerHTML).toContain("/workspace $");
    expect(card.innerHTML).toContain("shell-output");
  });

  it("renders File Diff card with colored additions and deletions", () => {
    const diff = "-old line\n+new line\n unchanged line";
    const card = renderToolCard({
      id: "tool-2",
      name: "edit_file",
      args: JSON.stringify({ TargetFile: "app.ts", ReplacementContent: diff }),
      output: "File updated successfully",
      status: "completed",
    });

    expect(card.innerHTML).toContain("文件修改 Diff");
    expect(card.innerHTML).toContain("app.ts");
    expect(card.innerHTML).toContain("diff-del");
    expect(card.innerHTML).toContain("-old line");
    expect(card.innerHTML).toContain("diff-add");
    expect(card.innerHTML).toContain("+new line");
  });

  it("renders Subagent delegation card", () => {
    const card = renderToolCard({
      id: "tool-3",
      name: "spawn_subagent",
      args: JSON.stringify({
        Role: "Code Reviewer",
        Prompt: "Review the PR changes for security issues",
      }),
      output: "Review completed: LGTM",
      status: "completed",
    });

    expect(card.innerHTML).toContain("子智能体协作: Code Reviewer");
    expect(card.innerHTML).toContain(
      "Review the PR changes for security issues",
    );
    expect(card.innerHTML).toContain("subagent-output");
  });

  it("renders Batch Tool execution card with items list", () => {
    const card = renderToolCard({
      id: "tool-4",
      name: "run_tool_batch",
      args: JSON.stringify({
        tools: [
          { name: "read_file", arguments: { path: "a.txt" } },
          { name: "grep_search", arguments: { query: "foo" } },
        ],
      }),
      output: "Batch execution done",
      status: "completed",
    });

    expect(card.innerHTML).toContain("批量工具并发执行 (2 项)");
    expect(card.innerHTML).toContain("read_file");
    expect(card.innerHTML).toContain("grep_search");
  });
});

describe("ChatStream Context & Subagent Extensions", () => {
  it("parses context ratio and token limits from turn_usage event", () => {
    const stream = new ChatStream();
    stream.consume({
      type: "turn_usage",
      prompt_tokens: 1500,
      completion_tokens: 300,
      total_tokens: 1800,
      context_usage: {
        estimated_tokens: 45000,
        max_input_length: 128000,
        context_usage_ratio: 35.15,
      },
    });

    expect(stream.turnUsage).toEqual({
      prompt_tokens: 1500,
      completion_tokens: 300,
      total_tokens: 1800,
    });
    expect(stream.contextUsage).toEqual({
      estimated_tokens: 45000,
      max_input_length: 128000,
      context_usage_ratio: 35.15,
    });
  });

  it("tracks subagent events and status transitions", () => {
    const stream = new ChatStream();
    stream.consume({
      type: "subagent",
      subagent_id: "worker-1",
      name: "Data Analyst",
      status: "running",
      task: "Process csv data",
    });

    expect(stream.subagents).toHaveLength(1);
    expect(stream.subagents[0].name).toBe("Data Analyst");
    expect(stream.subagents[0].status).toBe("running");

    // Status update
    stream.consume({
      type: "subagent",
      subagent_id: "worker-1",
      status: "completed",
    });
    expect(stream.subagents[0].status).toBe("completed");
  });
});

describe("ContextMonitor DOM Element", () => {
  it("renders progress bar and metrics", () => {
    const monitor = new ContextMonitor();
    monitor.update(
      { prompt_tokens: 100, completion_tokens: 50, total_tokens: 150 },
      {
        estimated_tokens: 50000,
        max_input_length: 100000,
        context_usage_ratio: 50,
      },
    );

    const html = monitor.element.innerHTML;
    expect(html).toContain("50%");
    expect(html).toContain("50,000 / 100,000");
    expect(html).toContain("入 100 · 出 50 · 计 150");
  });
});

describe("Default TL Provider and Model Selection", () => {
  it("initializes TLPanel with deepseek-v4-flash model default", async () => {
    const { TLPanel } = await import("../src/tl");
    const { ApiClient } = await import("../src/api");
    document.body.innerHTML =
      '<div id="tl-panel"></div><span id="chat-provider"></span>';
    const api = new ApiClient();
    const panel = new TLPanel(
      api,
      async (fn) => fn(),
      () => {},
    );
    panel.reset();

    const chatProviderEl = document.getElementById("chat-provider")!;
    expect(chatProviderEl.textContent).toContain("deepseek-v4-flash");
    const tlModelEl = document.getElementById("tl-model") as HTMLSelectElement;
    expect(tlModelEl.value).toBe("deepseek-v4-flash");
  });
});
