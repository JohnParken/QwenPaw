import { ApiClient } from "./api";
import { escapeHtml } from "./markdown";
import type { RecordValue } from "./chat";

export class CheckpointsPanel {
  private container: HTMLElement;

  constructor(
    private api: ApiClient,
    private run: (task: () => Promise<void>, success?: string) => Promise<void>,
    containerId = "checkpoints-panel-root",
  ) {
    let el = document.getElementById(containerId);
    if (!el) {
      el = document.createElement("div");
      el.id = containerId;
    }
    this.container = el;
    this.render();
  }

  get element(): HTMLElement {
    return this.container;
  }

  private render(): void {
    this.container.innerHTML = `
      <div class="checkpoints-container">
        <div class="checkpoints-header-row">
          <div>
            <h4>🔄 工作区检查点与快照 (Checkpoints & Rollback)</h4>
            <p class="hint">为当前会话保存或回滚工作区文件与会话状态，支持安全恢复与撤销修改。</p>
          </div>
          <div class="row">
            <button type="button" id="cp-refresh-btn" class="secondary">刷新快照</button>
            <button type="button" id="cp-create-btn" class="primary">+ 创建新快照</button>
          </div>
        </div>

        <div id="checkpoints-list" class="checkpoints-list">
          <p class="muted-hint">请先选择会话后加载检查点列表</p>
        </div>
      </div>
    `;

    this.container
      .querySelector("#cp-refresh-btn")
      ?.addEventListener("click", () => {
        void this.run(() => this.loadCheckpoints(), "快照列表已更新");
      });

    this.container
      .querySelector("#cp-create-btn")
      ?.addEventListener("click", () => {
        const name = prompt("请输入新快照名称 (例如: 修改前后备份):");
        if (!name) return;
        void this.run(async () => {
          const chatSelect = document.getElementById(
            "chats",
          ) as HTMLSelectElement;
          const sessionId = chatSelect?.value || "default";
          await this.api.request("/api/workspace/checkpoints/snapshot", {
            method: "POST",
            body: {
              session_id: sessionId,
              name,
              channel: "console",
            },
          });
          await this.loadCheckpoints();
        }, "快照已成功创建");
      });
  }

  async loadCheckpoints(): Promise<void> {
    const listEl =
      this.container.querySelector<HTMLElement>("#checkpoints-list");
    if (!listEl) return;
    const chatSelect = document.getElementById("chats") as HTMLSelectElement;
    const sessionId = chatSelect?.value;
    if (!sessionId) {
      listEl.innerHTML =
        '<p class="muted-hint">请先在左侧选择或新建一个会话</p>';
      return;
    }

    try {
      const res = await this.api.request<{ entries?: RecordValue[] }>(
        `/api/workspace/checkpoints/list?session_id=${encodeURIComponent(sessionId)}&channel=console`,
      );
      const entries = res.entries || [];
      if (entries.length === 0) {
        listEl.innerHTML =
          '<p class="muted-hint">当前会话暂无保存的快照检查点</p>';
        return;
      }

      listEl.innerHTML = "";
      for (const cp of entries) {
        const row = document.createElement("div");
        row.className = "checkpoint-item-row";
        row.innerHTML = `
          <div class="checkpoint-info">
            <span class="checkpoint-name">🏷️ ${escapeHtml(cp.name || cp.commit || "快照")}</span>
            <span class="checkpoint-commit">Commit: <code>${escapeHtml((cp.commit || "").slice(0, 8))}</code></span>
            <span class="checkpoint-time">${escapeHtml(cp.created_at || "")}</span>
          </div>
          <div class="checkpoint-actions">
            <button type="button" class="btn-restore-cp secondary">一键回滚 Rollback</button>
          </div>
        `;

        row.querySelector(".btn-restore-cp")?.addEventListener("click", () => {
          if (
            !confirm(
              `确认要将工作区回滚到快照 "${cp.name || cp.commit}" 吗？此操作将恢复文件。`,
            )
          ) {
            return;
          }
          void this.run(
            async () => {
              await this.api.request("/api/workspace/checkpoints/restore", {
                method: "POST",
                body: {
                  commit: cp.commit,
                  session_id: sessionId,
                  channel: "console",
                  include_files: true,
                  include_memory: true,
                },
              });
              await this.loadCheckpoints();
            },
            `已成功回滚到快照 ${cp.name || cp.commit}`,
          );
        });

        listEl.appendChild(row);
      }
    } catch {
      listEl.innerHTML =
        '<p class="muted-hint">检查点功能在当前环境未配置或暂无数据</p>';
    }
  }
}
