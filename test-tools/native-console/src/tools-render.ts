import { escapeHtml } from "./markdown";
import type { ToolCallItem } from "./chat";

export function renderToolCard(
  tool: ToolCallItem,
  existing?: HTMLElement,
): HTMLElement {
  const card = existing || document.createElement("div");
  const expanded =
    card.querySelector<HTMLDetailsElement>("details")?.open ?? true;
  card.className = "tool-card";
  card.dataset.toolId = tool.id;

  const statusClass = tool.status;
  const statusLabel: Record<string, string> = {
    preparing: "准备中…",
    awaiting_approval: "等待审批",
    running: "⚡ 执行中…",
    completed: "✓ 成功",
    failed: "✗ 失败",
    denied: "⊘ 已拒绝",
    unknown: "? 结果未知",
    cancelled: "Ⅱ 已取消",
  };
  const label = statusLabel[tool.status] || "? 未知状态";

  const finalize = (): HTMLElement => {
    const header = card.querySelector<HTMLElement>(".tool-card-header");
    const body = card.querySelector<HTMLElement>(".tool-card-body");
    if (header && body) {
      const details = document.createElement("details");
      details.className = "tool-card-details";
      details.open = expanded;
      const summary = document.createElement("summary");
      summary.className = "tool-card-summary";
      summary.append(header);
      details.append(summary, body);
      card.replaceChildren(details);
    }
    try {
      const result = JSON.parse(tool.output || "null");
      const file = result?.file || result?.result?.file;
      if (file?.id) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = `下载产物：${String(file.name || file.id)}`;
        button.dataset.fileId = String(file.id);
        card.querySelector(".tool-card-body")?.append(button);
      }
    } catch {
      /* Plain text results have no file reference. */
    }
    return card;
  };

  let parsedArgs: Record<string, any> = {};
  try {
    parsedArgs = JSON.parse(tool.args || "{}");
  } catch {
    // raw string
  }

  const name = tool.name.toLowerCase();

  // 1. Shell / terminal command
  if (
    name.includes("shell") ||
    name.includes("command") ||
    name.includes("bash")
  ) {
    const cmd = parsedArgs.command || parsedArgs.cmd || tool.args;
    const cwd = parsedArgs.cwd || parsedArgs.workdir || "";
    let output = tool.output || "";
    let exitCode: unknown;
    try {
      const result = JSON.parse(output);
      const execution = result?.result || result;
      if (
        typeof execution?.stdout === "string" ||
        typeof execution?.stderr === "string"
      ) {
        output =
          String(execution.stdout || "") + String(execution.stderr || "");
        exitCode = execution.exit_code;
      }
    } catch {
      /* Incremental log chunks remain plain text. */
    }

    card.innerHTML = `
      <div class="tool-card-header tool-header-shell">
        <span class="tool-name-tag">💻 终端命令 Shell</span>
        <span class="tool-status-badge ${statusClass}">${label}</span>
      </div>
      <div class="tool-card-body">
        <div class="tool-shell-cmd-row">
          ${cwd ? `<span class="tool-shell-cwd">${escapeHtml(cwd)} $</span>` : '<span class="tool-shell-prompt">$</span>'}
          <code class="tool-shell-cmd">${escapeHtml(String(cmd))}</code>
        </div>
        ${
          output
            ? `<div class="tool-output-box">
                <span class="tool-block-label">控制台输出 Output:${exitCode !== undefined ? ` · exit ${escapeHtml(String(exitCode))}` : ""}</span>
                <pre class="tool-code-pre shell-output">${escapeHtml(output)}</pre>
              </div>`
            : ""
        }
      </div>
    `;
    return finalize();
  }

  // 2. File I/O: write_file / edit_file / append_file
  if (
    name.includes("write_file") ||
    name.includes("edit_file") ||
    name.includes("append_file") ||
    name.includes("patch_file")
  ) {
    const filePath =
      parsedArgs.TargetFile ||
      parsedArgs.path ||
      parsedArgs.file ||
      parsedArgs.filename ||
      "";
    const content =
      parsedArgs.CodeContent ||
      parsedArgs.ReplacementContent ||
      parsedArgs.content ||
      parsedArgs.patch ||
      "";
    const isEdit = name.includes("edit") || name.includes("patch");

    // Render simple diff highlighting if available
    let contentHtml = "";
    if (content) {
      const lines = String(content).split("\n");
      const formattedLines = lines.map((line) => {
        if (line.startsWith("+")) {
          return `<span class="diff-line diff-add">${escapeHtml(line)}</span>`;
        } else if (line.startsWith("-")) {
          return `<span class="diff-line diff-del">${escapeHtml(line)}</span>`;
        }
        return `<span class="diff-line">${escapeHtml(line)}</span>`;
      });
      contentHtml = formattedLines.join("\n");
    }

    card.innerHTML = `
      <div class="tool-card-header tool-header-file">
        <span class="tool-name-tag">📝 文件修改 ${isEdit ? "Diff" : "Write"}</span>
        <span class="tool-status-badge ${statusClass}">${label}</span>
      </div>
      <div class="tool-card-body">
        <div class="tool-file-path-row">
          <span class="tool-path-icon">📄</span>
          <span class="tool-file-path">${escapeHtml(filePath || "未知路径")}</span>
        </div>
        ${
          contentHtml
            ? `<div class="tool-diff-container">
                <pre class="tool-diff-pre"><code>${contentHtml}</code></pre>
              </div>`
            : `<pre class="tool-code-pre">${escapeHtml(tool.args)}</pre>`
        }
        ${
          tool.output
            ? `<div class="tool-output-box">
                <span class="tool-block-label">结果 Result:</span>
                <pre class="tool-code-pre">${escapeHtml(tool.output)}</pre>
              </div>`
            : ""
        }
      </div>
    `;
    return finalize();
  }

  // 3. Subagent / Multi-agent delegation
  if (
    name.includes("subagent") ||
    name.includes("chat_with_agent") ||
    name.includes("submit_to_agent") ||
    name.includes("delegate")
  ) {
    const subName =
      parsedArgs.name ||
      parsedArgs.TypeName ||
      parsedArgs.Role ||
      parsedArgs.agent_id ||
      parsedArgs.to_agent ||
      "Subagent";
    const prompt =
      parsedArgs.Prompt ||
      parsedArgs.prompt ||
      parsedArgs.message ||
      parsedArgs.instruction ||
      "";
    card.innerHTML = `
      <div class="tool-card-header tool-header-subagent">
        <span class="tool-name-tag">🤖 子智能体协作: ${escapeHtml(String(subName))}</span>
        <span class="tool-status-badge ${statusClass}">${label}</span>
      </div>
      <div class="tool-card-body">
        <div class="subagent-prompt-box">
          <span class="tool-block-label">委托任务 Prompt:</span>
          <div class="subagent-prompt-text">${escapeHtml(String(prompt))}</div>
        </div>
        ${
          tool.output
            ? `<div class="tool-output-box">
                <span class="tool-block-label">子智能体回复 Response:</span>
                <pre class="tool-code-pre subagent-output">${escapeHtml(tool.output)}</pre>
              </div>`
            : ""
        }
      </div>
    `;
    return finalize();
  }

  // 4. Browser / Screenshot / Visual
  if (
    name.includes("screenshot") ||
    name.includes("browser") ||
    name.includes("view_image")
  ) {
    const candidate = String(parsedArgs.url || parsedArgs.Url || "");
    const url = /^https?:\/\//i.test(candidate) ? candidate : "";
    // Check if output has base64 image or image url
    let imgSrc = "";
    if (tool.output) {
      const match = tool.output.match(
        /(data:image\/[a-zA-Z]+;base64,[A-Za-z0-9+/=]+)/,
      );
      if (match) imgSrc = match[1];
      else if (
        tool.output.startsWith("http") &&
        /\.(png|jpg|jpeg|webp|gif)/i.test(tool.output)
      ) {
        imgSrc = tool.output.trim();
      }
    }
    card.innerHTML = `
      <div class="tool-card-header tool-header-browser">
        <span class="tool-name-tag">🌐 网页与视觉工具 ${escapeHtml(tool.name)}</span>
        <span class="tool-status-badge ${statusClass}">${label}</span>
      </div>
      <div class="tool-card-body">
        ${url ? `<div class="tool-url-row">🔗 目标 URL: <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a></div>` : ""}
        ${
          imgSrc
            ? `<div class="tool-image-preview">
                <img src="${escapeHtml(imgSrc)}" alt="Screenshot preview" class="screenshot-img" />
              </div>`
            : ""
        }
        ${
          tool.output && !imgSrc
            ? `<div class="tool-output-box">
                <span class="tool-block-label">输出 Output:</span>
                <pre class="tool-code-pre">${escapeHtml(tool.output)}</pre>
              </div>`
            : ""
        }
      </div>
    `;
    return finalize();
  }

  // 5. Batch tool execution: run_tool_batch
  if (name.includes("batch") || name.includes("run_tool_batch")) {
    const toolsList = Array.isArray(parsedArgs.tools) ? parsedArgs.tools : [];
    card.innerHTML = `
      <div class="tool-card-header tool-header-batch">
        <span class="tool-name-tag">📦 批量工具并发执行 (${toolsList.length} 项)</span>
        <span class="tool-status-badge ${statusClass}">${label}</span>
      </div>
      <div class="tool-card-body">
        <div class="tool-batch-list">
          ${toolsList
            .map(
              (t: any, idx: number) => `
            <div class="tool-batch-item">
              <span class="batch-num">#${idx + 1}</span>
              <span class="batch-name">${escapeHtml(t.name || "tool")}</span>
              <span class="batch-args">${escapeHtml(JSON.stringify(t.arguments || t.args || {}))}</span>
            </div>
          `,
            )
            .join("")}
        </div>
        ${
          tool.output
            ? `<div class="tool-output-box">
                <span class="tool-block-label">批量执行结果 Output:</span>
                <pre class="tool-code-pre">${escapeHtml(tool.output)}</pre>
              </div>`
            : ""
        }
      </div>
    `;
    return finalize();
  }

  // 6. Default generic tool card
  card.innerHTML = `
    <div class="tool-card-header">
      <span class="tool-name-tag">🔧 ${escapeHtml(tool.name)}</span>
      <span class="tool-status-badge ${statusClass}">${label}</span>
    </div>
    <div class="tool-card-body">
      <span class="tool-block-label">输入参数 Arguments:</span>
      <pre class="tool-code-pre">${escapeHtml(tool.args)}</pre>
      ${
        tool.output
          ? `<span class="tool-block-label">执行结果 Output:</span><pre class="tool-code-pre">${escapeHtml(tool.output)}</pre>`
          : ""
      }
    </div>
  `;
  return finalize();
}
