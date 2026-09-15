import { ApiClient } from "./api";
import { escapeHtml } from "./markdown";
import type { RecordValue } from "./chat";

export class InboxPanel {
  private container: HTMLElement;
  private unreadCount = 0;
  private onCountChange?: (count: number) => void;

  constructor(
    private api: ApiClient,
    private run: (task: () => Promise<void>, success?: string) => Promise<void>,
    containerId = "inbox-panel-root",
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

  setCountCallback(cb: (count: number) => void): void {
    this.onCountChange = cb;
  }

  private render(): void {
    this.container.innerHTML = `
      <div class="inbox-panel-container">
        <div class="inbox-toolbar">
          <div class="inbox-title-wrap">
            <h2>📥 收件箱与任务中心 (Inbox & Tasks)</h2>
            <span id="inbox-badge" class="badge-count">0</span>
          </div>
          <div class="inbox-actions-row">
            <button type="button" id="inbox-refresh-btn" class="primary">刷新收件箱</button>
            <button type="button" id="inbox-mark-all-btn" class="secondary">全部标记已读</button>
            <button type="button" id="inbox-new-task-btn" class="secondary">+ 提交后台任务</button>
          </div>
        </div>

        <div class="inbox-grid">
          <!-- Left: Events list -->
          <div class="inbox-column inbox-events-col">
            <div class="inbox-col-header">通知与主动事件</div>
            <div id="inbox-events-list" class="inbox-list-scroll">
              <p class="muted-hint">点击「刷新收件箱」获取最新事件</p>
            </div>
          </div>

          <!-- Middle: Centralized Approval Queue -->
          <div class="inbox-column inbox-approval-col">
            <div class="inbox-col-header">待人工审批队列 (Tool Approvals)</div>
            <div id="inbox-approval-list" class="inbox-list-scroll">
              <p class="muted-hint">暂无待审批项</p>
            </div>
          </div>

          <!-- Right: Trace Details / Background Tasks -->
          <div class="inbox-column inbox-trace-col">
            <div class="inbox-col-header">执行轨迹 / 任务详情 (Trace Viewer)</div>
            <div id="inbox-trace-detail" class="inbox-trace-box">
              <p class="muted-hint">选择左侧事件可查看完整的执行 Trace 详情</p>
            </div>
          </div>
        </div>

        <!-- Submit Background Task Modal/Form -->
        <dialog id="inbox-task-dialog" class="inbox-modal">
          <form id="inbox-task-form" method="dialog" class="inbox-modal-form">
            <h3>提交长时间运行的后台任务 (POST /console/chat/task)</h3>
            <p class="hint">任务将在后端以异步 Worker 模式执行，不占用当前前台流，执行完成后可通过收件箱查看轨迹。</p>
            <label>任务指令 Prompt:
              <textarea id="inbox-task-prompt" rows="4" required placeholder="例如：扫描整个工作区代码库，整理所有未使用的配置项并生成分析报告..."></textarea>
            </label>
            <label>超时时间 Timeout (秒):
              <input id="inbox-task-timeout" type="number" value="300" min="10" max="3600" required />
            </label>
            <div class="dialog-buttons">
              <button type="button" id="inbox-task-cancel" class="secondary">取消</button>
              <button type="submit" class="primary">提交执行</button>
            </div>
          </form>
        </dialog>
      </div>
    `;

    this.bindEvents();
  }

  private bindEvents(): void {
    const refreshBtn =
      this.container.querySelector<HTMLButtonElement>("#inbox-refresh-btn");
    refreshBtn?.addEventListener("click", () => {
      void this.run(() => this.loadAll(), "收件箱已刷新");
    });

    const markAllBtn = this.container.querySelector<HTMLButtonElement>(
      "#inbox-mark-all-btn",
    );
    markAllBtn?.addEventListener("click", () => {
      void this.run(async () => {
        await this.api.request("/api/console/inbox/read", {
          method: "POST",
          body: { all: true, event_ids: [] },
        });
        await this.loadAll();
      }, "所有通知已标记为已读");
    });

    const newTaskBtn = this.container.querySelector<HTMLButtonElement>(
      "#inbox-new-task-btn",
    );
    const dialog =
      this.container.querySelector<HTMLDialogElement>("#inbox-task-dialog");
    const cancelBtn =
      this.container.querySelector<HTMLButtonElement>("#inbox-task-cancel");
    const form =
      this.container.querySelector<HTMLFormElement>("#inbox-task-form");

    newTaskBtn?.addEventListener("click", () => {
      dialog?.showModal();
    });

    cancelBtn?.addEventListener("click", () => {
      dialog?.close();
    });

    form?.addEventListener("submit", (e) => {
      e.preventDefault();
      const prompt = this.container
        .querySelector<HTMLTextAreaElement>("#inbox-task-prompt")
        ?.value.trim();
      const timeout = Number(
        this.container.querySelector<HTMLInputElement>("#inbox-task-timeout")
          ?.value || 300,
      );
      if (!prompt) return;
      dialog?.close();

      void this.run(async () => {
        const res = await this.api.request<{ task_id: string }>(
          "/api/console/chat/task",
          {
            method: "POST",
            body: {
              input: [
                { role: "user", content: [{ type: "text", text: prompt }] },
              ],
              session_id: crypto.randomUUID(),
              user_id: "default",
              channel: "console",
              timeout,
            },
          },
        );
        this.trackBackgroundTask(res.task_id);
      }, "后台任务已成功提交");
    });
  }

  async loadAll(): Promise<void> {
    await Promise.allSettled([this.loadEvents(), this.loadApprovals()]);
  }

  async loadEvents(): Promise<void> {
    const listEl =
      this.container.querySelector<HTMLElement>("#inbox-events-list");
    if (!listEl) return;
    try {
      const res = await this.api.request<{ events?: RecordValue[] }>(
        "/api/console/inbox/events",
      );
      const events = res.events || [];
      this.unreadCount = events.filter((e) => !e.read).length;
      const badge = this.container.querySelector<HTMLElement>("#inbox-badge");
      if (badge) badge.textContent = String(this.unreadCount);
      if (this.onCountChange) this.onCountChange(this.unreadCount);

      if (events.length === 0) {
        listEl.innerHTML = '<p class="muted-hint">暂无收件箱事件</p>';
        return;
      }

      listEl.innerHTML = "";
      for (const ev of events) {
        const item = document.createElement("div");
        item.className = `inbox-event-item ${ev.read ? "read" : "unread"}`;
        item.innerHTML = `
          <div class="inbox-item-header">
            <span class="inbox-item-title">${escapeHtml(ev.title || ev.type || "通知")}</span>
            <span class="inbox-item-time">${escapeHtml(ev.created_at || "")}</span>
          </div>
          <div class="inbox-item-desc">${escapeHtml(ev.summary || ev.content || "")}</div>
          <div class="inbox-item-footer">
            ${ev.run_id ? `<button type="button" class="btn-trace-link" data-run-id="${escapeHtml(ev.run_id)}">查看 Trace</button>` : ""}
            ${!ev.read ? `<button type="button" class="btn-mark-read" data-id="${escapeHtml(ev.id)}">标为已读</button>` : ""}
          </div>
        `;

        item.querySelector(".btn-trace-link")?.addEventListener("click", () => {
          if (ev.run_id) void this.viewTrace(ev.run_id);
        });

        item.querySelector(".btn-mark-read")?.addEventListener("click", () => {
          void this.run(async () => {
            await this.api.request("/api/console/inbox/read", {
              method: "POST",
              body: { all: false, event_ids: [ev.id] },
            });
            await this.loadEvents();
          });
        });

        listEl.appendChild(item);
      }
    } catch {
      listEl.innerHTML =
        '<p class="muted-hint">无法获取收件箱事件 (接口可能未提供或为空)</p>';
    }
  }

  async loadApprovals(): Promise<void> {
    const listEl = this.container.querySelector<HTMLElement>(
      "#inbox-approval-list",
    );
    if (!listEl) return;
    try {
      const res = await this.api.request<{ pending_approvals: RecordValue[] }>(
        "/api/approval/list",
      );
      const list = res.pending_approvals || [];
      if (list.length === 0) {
        listEl.innerHTML =
          '<p class="muted-hint">暂无待人工审批的工具执行请求</p>';
        return;
      }

      listEl.innerHTML = "";
      for (const item of list) {
        const card = document.createElement("div");
        card.className = "inbox-approval-card";
        card.innerHTML = `
          <div class="approval-title">🛡️ ${escapeHtml(item.tool_name || "Tool")}</div>
          <div class="approval-info">
            <span>Session: ${escapeHtml(item.session_id ? item.session_id.slice(0, 8) : "-")}</span>
            <p>原因: ${escapeHtml(item.reasoning || item.reason || "高危操作需确认")}</p>
            <pre class="approval-args-pre">${escapeHtml(JSON.stringify(item.arguments || item.args || {}, null, 2))}</pre>
          </div>
          <div class="approval-btns">
            <button type="button" class="btn-approve-single primary">批准 Exact</button>
            <button type="button" class="btn-approve-similar secondary">批准 Similar</button>
            <button type="button" class="btn-deny-action btn-stop">拒绝 Deny</button>
          </div>
        `;

        card
          .querySelector(".btn-approve-single")
          ?.addEventListener("click", () => {
            void this.run(async () => {
              await this.api.request("/api/approval/approve", {
                method: "POST",
                body: {
                  request_id: item.request_id,
                  session_id: item.session_id,
                  scope: "exact",
                },
              });
              await this.loadApprovals();
            }, "已批准执行");
          });

        card
          .querySelector(".btn-approve-similar")
          ?.addEventListener("click", () => {
            void this.run(async () => {
              await this.api.request("/api/approval/approve", {
                method: "POST",
                body: {
                  request_id: item.request_id,
                  session_id: item.session_id,
                  scope: "similar",
                },
              });
              await this.loadApprovals();
            }, "已批准同类操作");
          });

        card
          .querySelector(".btn-deny-action")
          ?.addEventListener("click", () => {
            void this.run(async () => {
              await this.api.request("/api/approval/deny", {
                method: "POST",
                body: {
                  request_id: item.request_id,
                  session_id: item.session_id,
                  reason: "管理员在收件箱拒绝",
                },
              });
              await this.loadApprovals();
            }, "已拒绝执行");
          });

        listEl.appendChild(card);
      }
    } catch {
      listEl.innerHTML = '<p class="muted-hint">审批接口未启用或为空</p>';
    }
  }

  async viewTrace(runId: string): Promise<void> {
    const traceBox = this.container.querySelector<HTMLElement>(
      "#inbox-trace-detail",
    );
    if (!traceBox) return;
    traceBox.innerHTML = '<p class="muted-hint">加载执行轨迹中…</p>';
    try {
      const trace = await this.api.request<RecordValue>(
        `/api/console/inbox/traces/${encodeURIComponent(runId)}`,
      );
      traceBox.innerHTML = `
        <div class="trace-header">
          <strong>Run ID:</strong> <code>${escapeHtml(runId)}</code>
        </div>
        <pre class="trace-content-pre"><code>${escapeHtml(JSON.stringify(trace, null, 2))}</code></pre>
      `;
    } catch (err: any) {
      traceBox.innerHTML = `<p class="error">加载 Trace 失败: ${escapeHtml(err?.message || String(err))}</p>`;
    }
  }

  trackBackgroundTask(taskId: string): void {
    const traceBox = this.container.querySelector<HTMLElement>(
      "#inbox-trace-detail",
    );
    if (!traceBox) return;

    traceBox.innerHTML = `
      <div class="bg-task-progress-card">
        <h4>🚀 正在监控后台任务: <code>${escapeHtml(taskId)}</code></h4>
        <div class="pulse-dot"></div>
        <span id="bg-task-status-text">状态: running...</span>
        <pre id="bg-task-result-pre" style="margin-top:8px; max-height:200px; overflow:auto;"></pre>
      </div>
    `;

    const poll = async () => {
      try {
        const res = await this.api.request<RecordValue>(
          `/api/console/chat/task/${encodeURIComponent(taskId)}`,
        );
        const statusEl = document.getElementById("bg-task-status-text");
        const resEl = document.getElementById("bg-task-result-pre");
        if (statusEl) statusEl.textContent = `状态: ${res.status}`;
        if (res.status === "finished") {
          if (resEl)
            resEl.textContent = JSON.stringify(res.result || {}, null, 2);
          void this.loadEvents();
          return;
        }
        if (res.status === "failed") {
          if (resEl) resEl.textContent = "任务执行失败";
          return;
        }
        setTimeout(poll, 2000);
      } catch {
        // Stop polling on error
      }
    };
    setTimeout(poll, 1500);
  }
}
