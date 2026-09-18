/**
 * Lightweight, zero-dependency Markdown renderer with safe HTML escaping,
 * code blocks with copy buttons, tables, and typography support.
 */

export function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderInline(text: string): string {
  let res = escapeHtml(text);
  // Inline code: `code`
  res = res.replace(/`([^`]+)`/g, '<code class="md-inline-code">$1</code>');
  // Bold: **text**
  res = res.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  // Italic: *text*
  res = res.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  // Strikethrough: ~~text~~
  res = res.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  // Links: [label](url)
  res = res.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer" class="md-link">$1</a>',
  );
  return res;
}

export function renderMarkdownToHtml(markdown: string): string {
  if (!markdown) return "";
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const htmlParts: string[] = [];

  let inCodeBlock = false;
  let codeLang = "";
  let codeLines: string[] = [];

  let inList: "ul" | "ol" | null = null;
  let inTable = false;

  const closeList = () => {
    if (inList) {
      htmlParts.push(`</${inList}>`);
      inList = null;
    }
  };

  const closeTable = () => {
    if (inTable) {
      htmlParts.push("</tbody></table></div>");
      inTable = false;
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Fenced code block start or end
    if (line.trim().startsWith("```")) {
      if (!inCodeBlock) {
        closeList();
        closeTable();
        inCodeBlock = true;
        codeLang = line.trim().slice(3).trim();
        codeLines = [];
        continue;
      } else {
        inCodeBlock = false;
        const rawCode = codeLines.join("\n");
        const escaped = escapeHtml(rawCode);
        const langDisplay = codeLang || "text";
        htmlParts.push(`
          <div class="code-block-container" data-lang="${escapeHtml(langDisplay)}">
            <div class="code-block-header">
              <span class="code-block-lang">${escapeHtml(langDisplay)}</span>
              <button type="button" class="code-block-copy-btn" title="复制代码">复制</button>
            </div>
            <pre class="code-block-pre"><code>${escaped}</code></pre>
          </div>
        `);
        codeLang = "";
        codeLines = [];
        continue;
      }
    }

    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }

    const trimmed = line.trim();

    // Table row
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      closeList();
      const cells = trimmed
        .slice(1, -1)
        .split("|")
        .map((c) => c.trim());
      // Check if it's separator row like |---|---|
      const isSep = cells.every((c) => /^:?-+:?$/.test(c));
      if (!inTable) {
        inTable = true;
        htmlParts.push(
          '<div class="md-table-wrapper"><table class="md-table">',
        );
        htmlParts.push(
          "<thead><tr>" +
            cells.map((c) => `<th>${renderInline(c)}</th>`).join("") +
            "</tr></thead><tbody>",
        );
        continue;
      } else if (isSep) {
        continue;
      } else {
        htmlParts.push(
          "<tr>" +
            cells.map((c) => `<td>${renderInline(c)}</td>`).join("") +
            "</tr>",
        );
        continue;
      }
    } else {
      closeTable();
    }

    // Blank line
    if (!trimmed) {
      closeList();
      continue;
    }

    // Headers
    const hMatch = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (hMatch) {
      closeList();
      const level = hMatch[1].length;
      htmlParts.push(
        `<h${level} class="md-h${level}">${renderInline(hMatch[2])}</h${level}>`,
      );
      continue;
    }

    // Blockquote
    if (trimmed.startsWith("> ")) {
      closeList();
      htmlParts.push(
        `<blockquote class="md-blockquote">${renderInline(trimmed.slice(2))}</blockquote>`,
      );
      continue;
    }

    // Unordered list
    const ulMatch = trimmed.match(/^[-*+]\s+(.*)$/);
    if (ulMatch) {
      if (inList !== "ul") {
        closeList();
        inList = "ul";
        htmlParts.push('<ul class="md-ul">');
      }
      htmlParts.push(`<li class="md-li">${renderInline(ulMatch[1])}</li>`);
      continue;
    }

    // Ordered list
    const olMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
    if (olMatch) {
      if (inList !== "ol") {
        closeList();
        inList = "ol";
        htmlParts.push('<ol class="md-ol">');
      }
      htmlParts.push(`<li class="md-li">${renderInline(olMatch[2])}</li>`);
      continue;
    }

    closeList();

    // Horizontal rule
    if (/^(\*\*\*|---|___)$/.test(trimmed)) {
      htmlParts.push('<hr class="md-hr" />');
      continue;
    }

    // Normal paragraph
    htmlParts.push(`<p class="md-p">${renderInline(line)}</p>`);
  }

  // Close any unclosed code blocks or lists/tables
  if (inCodeBlock) {
    const rawCode = codeLines.join("\n");
    const escaped = escapeHtml(rawCode);
    htmlParts.push(`
      <div class="code-block-container" data-lang="${escapeHtml(codeLang || "text")}">
        <div class="code-block-header">
          <span class="code-block-lang">${escapeHtml(codeLang || "text")} (生成中)</span>
          <button type="button" class="code-block-copy-btn" title="复制代码">复制</button>
        </div>
        <pre class="code-block-pre"><code>${escaped}</code></pre>
      </div>
    `);
  }
  closeList();
  closeTable();

  return htmlParts.join("");
}

export function bindMarkdownEvents(container: HTMLElement): void {
  container
    .querySelectorAll<HTMLButtonElement>(".code-block-copy-btn")
    .forEach((btn) => {
      if (btn.dataset.bound === "1") return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", () => {
        const pre = btn.closest(".code-block-container")?.querySelector("code");
        if (pre) {
          void navigator.clipboard.writeText(pre.textContent || "").then(() => {
            const orig = btn.textContent;
            btn.textContent = "✓ 已复制";
            btn.classList.add("copied");
            setTimeout(() => {
              btn.textContent = orig;
              btn.classList.remove("copied");
            }, 1500);
          });
        }
      });
    });
}
