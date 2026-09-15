import "./style.css";
import { ApiClient } from "./api";
import { TLPanel } from "./tl";
import { ChatStream, textContent, type RecordValue } from "./chat";
import {
  renderMarkdownToHtml,
  bindMarkdownEvents,
  escapeHtml,
} from "./markdown";
import { renderToolCard } from "./tools-render";
import { ContextMonitor } from "./context-monitor";
import { InboxPanel } from "./inbox";
import { CheckpointsPanel } from "./checkpoints";
import { SkillsPanel } from "./skills";

const api = new ApiClient();
const $ = <T extends HTMLElement = HTMLElement>(id: string) =>
  document.getElementById(id) as T;
const input = (id: string) => $<HTMLInputElement>(id).value.trim();
const value = (id: string) => $<HTMLInputElement>(id).value;
const json = (data: unknown) => JSON.stringify(data, null, 2);
const query = (path: string, params: Record<string, string | number>) =>
  "/api" +
  path +
  "?" +
  new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = "") => {
  const el = document.createElement(tag);
  el.textContent = text;
  return el;
};

$("app").innerHTML = `
  <!-- Left App Shell Sidebar -->
  <aside class="app-sidebar">
    <div class="sidebar-brand">
      <div class="brand-logo-wrap">
        <div class="brand-paw-icon">🐾</div>
        <div class="brand-titles">
          <h1>QwenPaw <span class="brand-ver">Native</span></h1>
          <p class="brand-subtitle">轻量原生测试验证台</p>
        </div>
      </div>
    </div>

    <!-- Agent Selector Card -->
    <div class="sidebar-agent-card">
      <div class="agent-label-row">
        <span>当前生效 Agent</span>
        <span id="agent-type-pill" class="agent-badge-pill">Active</span>
      </div>
      <select id="agent" title="切换当前 Agent">
        <option value="default">default</option>
      </select>
    </div>

    <!-- Navigation Tabs -->
    <nav class="sidebar-nav" aria-label="功能页">
      <button data-tab="chat" aria-current="page"><span class="nav-icon">💬</span> 核心会话</button>
      <button data-tab="files"><span class="nav-icon">📁</span> 工作区文件</button>
      <button data-tab="inbox"><span class="nav-icon">📥</span> 收件箱与任务</button>
      <button data-tab="skills"><span class="nav-icon">🧩</span> 智能体技能</button>
      <button data-tab="manage"><span class="nav-icon">⚙️</span> 管理与配置</button>
    </nav>

    <!-- Sidebar Sessions List -->
    <div class="sidebar-sessions-section">
      <div class="sessions-header">
        <span class="sessions-title">会话列表</span>
        <div class="sessions-header-actions">
          <button id="new-chat" class="btn-icon-sm" title="新建会话">+ 新建</button>
          <button id="refresh-chats" class="btn-icon-sm" title="刷新会话">刷新</button>
        </div>
      </div>
      <div id="sidebar-sessions-list" class="sidebar-sessions-list"></div>
      <!-- Hidden accessible select for test compatibility -->
      <select id="chats" class="sr-only">
        <option value="">选择会话</option>
      </select>
    </div>

    <!-- Sidebar Footer -->
    <div class="sidebar-footer">
      <button id="toggle-connect-panel" class="connection-pill-btn" type="button" title="点击展开/收起连接凭证">
        <span id="conn-dot" class="status-dot"></span>
        <span id="conn-text">未连接</span>
      </button>
    </div>
  </aside>

  <!-- Main Content Layout -->
  <div class="app-main-content">
    <!-- Top Header -->
    <header class="app-header">
      <div class="header-left">
        <span id="chat-identity" class="current-chat-name">未选择会话</span>
        <span id="chat-provider" class="header-tag accent">TL: deepseek-v4-flash</span>
        <span id="active-agent-badge" class="header-tag">Agent: default</span>
      </div>
      <div class="header-right">
        <!-- Context Monitor -->
        <div id="context-monitor-root"></div>
        <!-- Tool Guard Approval Level -->
        <div class="approval-level-box" title="Tool Guard 工具安全审批策略">
          <label>审批策略</label>
          <select id="approval-level">
            <option value="AUTO">AUTO (全自动免密)</option>
            <option value="SMART" selected>SMART (智能高危审批)</option>
            <option value="STRICT">STRICT (严格全部审批)</option>
            <option value="OFF">OFF (关闭审批限制)</option>
          </select>
        </div>
        <span id="stream-status" class="header-tag">流式状态：空闲</span>
        <button id="stop" disabled class="btn-stop">停止生成</button>
        <button id="toggle-logs-btn" class="header-btn" type="button">
          <span>📋 协议日志</span>
          <span id="log-count" class="badge-count">0</span>
        </button>
      </div>
    </header>

    <!-- Connection & Auth Settings (Card at top, easily accessible) -->
    <section id="connection-panel" class="connection card" style="margin: 10px 24px 0; padding: 10px 14px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: var(--radius); box-shadow: var(--app-shadow-sm);">
      <fieldset id="connection-fields" style="border:none;padding:0;margin:0;">
        <legend class="sr-only">连接现有后端</legend>
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <span style="font-size:12px; font-weight:600; color:var(--app-text-secondary);">🔗 后端连接与凭据</span>
          <p id="connection-status" style="margin:0; font-size:12px; color:var(--app-text-tertiary);">开发代理默认连接 http://127.0.0.1:8088；真实请求仅由操作触发。</p>
        </div>
        <div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center;">
          <form id="connect-form" class="row" style="display:inline-flex; gap:8px; align-items:center;">
            <label style="display:inline-flex; align-items:center; gap:4px; font-size:12px;">访问令牌<input id="token" type="password" autocomplete="off" placeholder="未启用鉴权可留空" style="padding:4px 8px; font-size:12px; width:180px;"></label>
            <button class="primary" style="padding:4px 12px; font-size:12px;">连接 / 检查</button>
            <button id="logout" type="button" class="secondary" style="padding:4px 10px; font-size:12px;">清除凭据</button>
          </form>
          <details style="display:inline-block; font-size:12px;">
            <summary style="cursor:pointer; color:var(--app-accent-text); font-weight:500;">使用账号密码登录</summary>
            <form id="login-form" class="row" style="display:inline-flex; gap:8px; align-items:center; margin-top:4px;">
              <label style="font-size:12px;">用户名<input id="username" autocomplete="username" required style="padding:3px 6px; font-size:12px; width:100px;"></label>
              <label style="font-size:12px;">密码<input id="password" type="password" autocomplete="current-password" required style="padding:3px 6px; font-size:12px; width:110px;"></label>
              <button class="primary" style="padding:3px 10px; font-size:12px;">登录</button>
            </form>
          </details>
        </div>
      </fieldset>
    </section>

    <!-- Notification Banner -->
    <div id="notice" role="status" aria-live="polite">就绪。先连接后端，再选择需要验证的流程。</div>

    <!-- Main Views Body -->
    <div class="app-view-body">
      <fieldset id="work-fields" style="border:none;padding:0;margin:0;display:contents;">
        <legend class="sr-only">功能验证</legend>

        <!-- View 1: Chat -->
        <section id="chat" class="tab">
          <div id="messages" class="chat-messages-scroll" role="log" aria-label="聊天消息"></div>

          <!-- Quick Test Presets Bar -->
          <div class="chat-presets-bar">
            <span class="presets-label">⚡ 快捷能力测试:</span>
            <button type="button" class="preset-chip" data-prompt="你好，请介绍一下 QwenPaw Agent 的主要功能和你能使用的工具。">基础问答</button>
            <button type="button" class="preset-chip" data-prompt="请详细推演并解答经典的农夫、狼、羊、白菜过河问题，给出每一步的状态变换和思考逻辑。">深度推理 (R1)</button>
            <button type="button" class="preset-chip" data-prompt="请在当前工作区创建一个 test-note.txt 文件并写入当前时间，然后再读取它。">文件读写工具</button>
            <button type="button" class="preset-chip" data-prompt="请执行终端命令查看当前目录下的文件列表和 Node 版本。">Shell 工具</button>
            <button type="button" class="preset-chip" data-prompt="请执行终端高危命令 cat /etc/hosts，测试工具安全审批拦截。">触发安全审批</button>
          </div>

          <!-- Chat Input Bar -->
          <form id="send-form" class="chat-input-bar">
            <div class="chat-input-wrapper">
              <textarea id="prompt" rows="2" placeholder="输入给 QwenPaw Agent 的指令... (Enter 发送，Shift+Enter 换行)" required></textarea>
              <div class="input-controls-row">
                <div class="input-controls-left">
                  <label class="file-upload-btn-label">📎 上传附件<input id="chat-file" type="file"></label>
                  <span id="chat-file-badge" class="attached-filename"></span>
                  <button type="button" id="history" class="header-btn" style="padding:3px 8px; font-size:11px;">加载历史</button>
                </div>
                <div class="input-controls-right">
                  <button id="send" class="btn-send">发送 ↵</button>
                </div>
              </div>
            </div>
          </form>
        </section>

        <!-- View 2: Files -->
        <section id="files" class="tab" hidden>
          <div class="workspace-card">
            <div class="workspace-toolbar">
              <label style="display:flex; align-items:center; gap:8px; font-size:13px; font-weight:600;">文件根目录:
                <select id="file-root" style="padding:4px 8px; border-radius:6px; border:1px solid var(--app-border);">
                  <option value="workspace">Agent workspace</option>
                  <option value="project">当前会话 project</option>
                </select>
              </label>
              <form id="browse-form" class="row" style="margin:0;">
                <label style="display:flex; align-items:center; gap:6px; font-size:13px;">目录路径:
                  <input id="directory" placeholder="空值为根目录" style="padding:4px 8px; width:200px;">
                </label>
                <button class="primary">浏览目录</button>
                <button type="button" id="more-files" disabled class="secondary">下一页</button>
              </form>
            </div>
            <div id="file-list" class="file-list"></div>
          </div>

          <div class="workspace-card">
            <form id="open-form" class="row" style="margin:0;">
              <label style="display:flex; align-items:center; gap:6px; font-size:13px; flex:1;">文件路径:
                <input id="file-path" placeholder="从列表选择或输入相对路径" required style="padding:4px 8px; width:100%;">
              </label>
              <button class="primary">读取文件</button>
            </form>
            <p id="file-version" class="muted">未读取文件；保存使用 ETag 防止覆盖其他修改。</p>
            <div class="file-editor-area">
              <label style="font-weight:600; font-size:12px; color:var(--app-text-secondary);">文本内容
                <textarea id="file-content" rows="12" spellcheck="false"></textarea>
              </label>
              <div class="row">
                <button id="save-file" class="primary">保存文件</button>
                <button id="download-file" class="secondary">下载文件</button>
              </div>
            </div>
          </div>

          <form id="upload-form" class="workspace-card">
            <h3 style="margin:0; font-size:14px;">上传文件到当前目录</h3>
            <input id="upload-files" type="file" multiple required>
            <p class="hint" style="margin:0;">同名文件采用 rename 策略。文本编辑上限 2 MiB；二进制文件使用下载。</p>
            <button class="primary" style="align-self:flex-start;">上传文件</button>
          </form>

          <div id="checkpoints-mount"></div>
        </section>

        <!-- View 3: Inbox -->
        <section id="inbox" class="tab" hidden>
          <div id="inbox-mount"></div>
        </section>

        <!-- View 4: Skills -->
        <section id="skills" class="tab" hidden>
          <div id="skills-mount"></div>
        </section>

        <!-- View 5: Manage -->
        <section id="manage" class="tab" hidden>
          <div class="workspace-card" style="gap:10px;">
            <div class="row">
              <button id="load-models" class="primary">读取模型配置</button>
              <button id="load-channels" class="secondary">读取渠道列表</button>
              <button id="load-cron" class="secondary">读取定时任务</button>
            </div>
            <pre id="management-result" class="result">尚未读取配置</pre>
          </div>
          <div class="forms-grid">
            <form id="model-form" class="subcard">
              <h3>设置 Agent 模型</h3>
              <label>Provider ID<input id="provider-id" value="tlprovider" required placeholder="例如 tlprovider 或 tlproxy"></label>
              <label>Model ID<input id="model-id" value="deepseek-v4-flash" required placeholder="例如 deepseek-v4-flash"></label>
              <button class="primary">保存模型选择</button>
            </form>
            <form id="provider-form" class="subcard">
              <h3>Provider 连接配置</h3>
              <label>Provider ID<input id="config-provider-id" value="tlprovider" required placeholder="例如 tlprovider 或 tlproxy"></label>
              <label>Base URL<input id="provider-url" type="url" value="http://127.0.0.1:8089" required></label>
              <label>API Key<input id="provider-key" type="password" autocomplete="off" placeholder="留空时不提交此字段"></label>
              <button class="primary">保存 Provider 配置</button>
            </form>
          </div>
          <div id="tl-panel"></div>
          <form id="channel-form" class="subcard">
            <h3>渠道配置</h3>
            <div class="row">
              <label>渠道<select id="channel-name"><option value="console">console</option></select></label>
              <button id="read-channel" type="button" class="secondary">读取该渠道</button>
            </div>
            <label>配置 JSON<textarea id="channel-json" rows="7" spellcheck="false" placeholder="先读取渠道配置，再编辑"></textarea></label>
            <button class="primary">保存渠道配置</button>
          </form>
          <div class="subcard">
            <h3>定时任务</h3>
            <div id="cron-list" style="display:flex; flex-direction:column; gap:6px;"></div>
            <form id="cron-form" style="display:flex; flex-direction:column; gap:8px; margin-top:8px;">
              <div class="row">
                <label>任务名称<input id="cron-name" required></label>
                <label>Cron 表达式<input id="cron-expression" value="0 9 * * *" required></label>
                <label>时区<input id="cron-timezone" value="Asia/Shanghai" required></label>
              </div>
              <label>通知文本<input id="cron-text" required></label>
              <button class="primary">创建（默认暂停）</button>
            </form>
          </div>
        </section>
      </fieldset>

      <!-- Protocol & SSE Logs Drawer (Side Panel) -->
      <aside id="logs-drawer" class="logs-drawer">
        <div class="logs-drawer-header">
          <h2>📋 接口与 SSE 请求日志</h2>
          <div style="display:flex; gap:6px;">
            <button id="export-logs" class="secondary" style="padding:3px 8px; font-size:11px;">导出</button>
            <button id="clear-logs" class="secondary" style="padding:3px 8px; font-size:11px;">清空</button>
            <button id="close-logs-btn" class="secondary" style="padding:3px 8px; font-size:11px;">✕</button>
          </div>
        </div>
        <div class="logs-drawer-search">
          <input id="log-filter" placeholder="筛选接口，例如 /console/chat 或 401">
        </div>
        <div id="log-list" class="logs-drawer-list"></div>
      </aside>
    </div>
  </div>
`;

let busy = false;
let streamController: AbortController | null = null;
let chats: RecordValue[] = [];
let currentChat: RecordValue | undefined;
let loadedFile: {
  path: string;
  root: string;
  chatId: string;
  etag: string;
} | null = null;
let nextCursor = "";
let listedDirectory = "";
let loadedChannel = "";
let knownChannels: string[] = [];

let activeBubbleEl: HTMLElement | null = null;
let activeOutputPre: HTMLElement | null = null;
let activeStream: ChatStream | null = null;
let approvalPollTimer: any = null;

const notice = (message: string, error = false) => {
  $("notice").textContent = String(api.safe(message));
  $("notice").classList.toggle("error", error);
};

async function run(
  task: () => Promise<void>,
  success = "操作完成",
): Promise<void> {
  if (busy) return;
  busy = true;
  $<HTMLFieldSetElement>("work-fields").disabled = true;
  $<HTMLFieldSetElement>("connection-fields").disabled = true;
  notice("请求处理中…");
  try {
    await task();
    if (success) notice(success);
  } catch (error) {
    notice(error instanceof Error ? error.message : String(error), true);
  } finally {
    busy = false;
    $<HTMLFieldSetElement>("work-fields").disabled = false;
    $<HTMLFieldSetElement>("connection-fields").disabled = false;
  }
}

function action(id: string, task: () => Promise<void>, success?: string): void {
  const element = $(id);
  element.addEventListener(
    element.tagName === "FORM" ? "submit" : "click",
    (event) => {
      event.preventDefault();
      void run(task, success);
    },
  );
}

function selectOptions(
  id: string,
  items: { value: string; label: string }[],
  selected = "",
): void {
  const select = $<HTMLSelectElement>(id);
  select.replaceChildren(
    ...items.map((item) => {
      const option = node("option", item.label);
      option.value = item.value;
      return option;
    }),
  );
  if (items.some((item) => item.value === selected)) select.value = selected;
}

const tl = new TLPanel(api, run, (data) => managementResult(data));
const contextMonitor = new ContextMonitor("context-monitor-root");
const inboxPanel = new InboxPanel(api, run, "inbox-mount");
const checkpointsPanel = new CheckpointsPanel(api, run, "checkpoints-mount");
const skillsPanel = new SkillsPanel(api, run, "skills-mount");

function renderSidebarSessions(): void {
  const container = $("sidebar-sessions-list");
  container.replaceChildren();
  for (const chat of chats) {
    const item = node("button");
    item.type = "button";
    item.className =
      "sidebar-session-item" + (currentChat?.id === chat.id ? " active" : "");
    item.dataset.id = chat.id;
    const title = node("span", chat.name || `会话 ${chat.id.slice(0, 8)}`);
    title.className = "session-name-truncate";
    item.append(title);
    item.addEventListener("click", () => {
      $<HTMLSelectElement>("chats").value = chat.id;
      updateChatSelection();
    });
    container.append(item);
  }
}

function resetContext(): void {
  tl.reset();
  contextMonitor.update(null, null);
  chats = [];
  currentChat = undefined;
  loadedFile = null;
  loadedChannel = "";
  nextCursor = "";
  knownChannels = [];
  $("messages").replaceChildren();
  $("file-list").replaceChildren();
  $("cron-list").replaceChildren();
  $("sidebar-sessions-list").replaceChildren();
  $<HTMLTextAreaElement>("file-content").value = "";
  $<HTMLTextAreaElement>("channel-json").value = "";
  $("management-result").textContent = "尚未读取配置";
  $("file-version").textContent = "未读取文件";
  $("chat-identity").textContent = "未选择会话";
  selectOptions("chats", [{ value: "", label: "选择会话" }]);
}

async function connect(): Promise<void> {
  api.token = value("token");
  resetContext();
  const status = await api.request<RecordValue>("/api/auth/status");
  if (status.enabled) await api.request("/api/auth/verify");
  const result = await api.request<{ agents: RecordValue[] }>("/api/agents");
  const agents = result.agents.filter(
    (agent) => agent.available_in_chat !== false,
  );
  selectOptions(
    "agent",
    agents.map((agent) => ({
      value: agent.id,
      label: agent.name || agent.id,
    })),
    api.agent,
  );
  api.agent = input("agent") || "default";
  $("active-agent-badge").textContent = `Agent: ${api.agent}`;
  await tl.load();
  $("connection-status").textContent =
    `已连接 · ${status.enabled ? "鉴权已验证" : "未启用鉴权"} · Agent: ${api.agent}`;
  $("conn-text").textContent = `已连接 · ${api.agent}`;
  $("conn-dot").className = "status-dot connected";
  void inboxPanel.loadAll();
  void skillsPanel.loadSkills();
}

action("connect-form", connect, "连接成功");

action(
  "login-form",
  async () => {
    const result = await api.request<{ token: string }>("/api/auth/login", {
      method: "POST",
      body: { username: input("username"), password: value("password") },
    });
    $<HTMLInputElement>("password").value = "";
    $<HTMLInputElement>("token").value = result.token;
    await connect();
  },
  "登录成功",
);

$("logout").addEventListener("click", () => {
  api.token = "";
  $<HTMLInputElement>("token").value = "";
  $<HTMLInputElement>("password").value = "";
  resetContext();
  api.clear();
  $("connection-status").textContent = "已清除本页凭据";
  $("conn-text").textContent = "未连接";
  $("conn-dot").className = "status-dot";
});

$("agent").addEventListener("change", () => {
  api.agent = input("agent");
  $("active-agent-badge").textContent = `Agent: ${api.agent}`;
  resetContext();
  void run(() => tl.load(), `已切换到 Agent ${api.agent}`);
});

document.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach((button) =>
  button.addEventListener("click", () => {
    document.querySelectorAll<HTMLElement>(".tab").forEach((tab) => {
      tab.hidden = tab.id !== button.dataset.tab;
    });
    document
      .querySelectorAll("[data-tab]")
      .forEach((item) => item.removeAttribute("aria-current"));
    button.setAttribute("aria-current", "page");
    if (button.dataset.tab === "inbox") void inboxPanel.loadAll();
    if (button.dataset.tab === "skills") void skillsPanel.loadSkills();
    if (button.dataset.tab === "files") void checkpointsPanel.loadCheckpoints();
  }),
);

// Toggle Connection Panel
$("toggle-connect-panel").addEventListener("click", () => {
  const panel = $("connection-panel");
  panel.hidden = !panel.hidden;
});

// Logs Drawer
$("toggle-logs-btn").addEventListener("click", () => {
  $("logs-drawer").classList.toggle("closed");
});
$("close-logs-btn").addEventListener("click", () => {
  $("logs-drawer").classList.add("closed");
});

// Preset Chips
document.querySelectorAll<HTMLButtonElement>(".preset-chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    const prompt = btn.dataset.prompt;
    if (prompt) {
      $<HTMLTextAreaElement>("prompt").value = prompt;
      $<HTMLTextAreaElement>("prompt").focus();
    }
  });
});

// Textarea Enter send
$<HTMLTextAreaElement>("prompt").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $<HTMLButtonElement>("send").click();
  }
});

// File attachment badge
$<HTMLInputElement>("chat-file").addEventListener("change", () => {
  const file = $<HTMLInputElement>("chat-file").files?.[0];
  $("chat-file-badge").textContent = file ? `📎 ${file.name}` : "";
});

function message(
  role: string,
  text: string,
): { article: HTMLElement; pre: HTMLElement } {
  const isUser = role === "user";
  const article = node("article");
  article.className = `message-bubble-row ${isUser ? "user message user" : "assistant message assistant"}`;

  const avatar = node("div", isUser ? "U" : "🐾");
  avatar.className = `msg-avatar ${isUser ? "user-avatar" : "agent-avatar"}`;

  const contentWrapper = node("div");
  contentWrapper.className = "msg-content-wrapper";

  const metaRow = node("div");
  metaRow.className = "msg-meta-row";
  const senderSpan = node(
    "span",
    isUser ? "User" : `QwenPaw Agent (${api.agent || "default"})`,
  );
  senderSpan.className = "msg-sender-name";
  metaRow.append(senderSpan);

  const cardBody = node("div");
  cardBody.className = "msg-card-body";

  const pre = node("pre", text);
  cardBody.append(pre);

  contentWrapper.append(metaRow, cardBody);
  article.append(avatar, contentWrapper);

  $("messages").append(article);
  article.scrollIntoView({ block: "nearest" });
  return { article, pre };
}

async function resolveApproval(
  requestId: string,
  scope: "exact" | "similar",
): Promise<void> {
  if (!currentChat) return;
  await api.request("/api/approval/approve", {
    method: "POST",
    body: {
      request_id: requestId,
      session_id: currentChat.session_id,
      scope,
    },
  });
  notice(`已批准工具执行 (scope: ${scope})`);
  if (activeStream) activeStream.pendingApproval = null;
  if (activeBubbleEl && activeOutputPre && activeStream) {
    updateAssistantBubble(activeBubbleEl, activeStream, activeOutputPre);
  }
}

async function denyApproval(requestId: string): Promise<void> {
  if (!currentChat) return;
  await api.request("/api/approval/deny", {
    method: "POST",
    body: {
      request_id: requestId,
      session_id: currentChat.session_id,
      reason: "用户拒绝执行",
    },
  });
  notice("已拒绝工具执行");
  if (activeStream) activeStream.pendingApproval = null;
  if (activeBubbleEl && activeOutputPre && activeStream) {
    updateAssistantBubble(activeBubbleEl, activeStream, activeOutputPre);
  }
}

function updateAssistantBubble(
  article: HTMLElement,
  stream: ChatStream,
  outputPre: HTMLElement,
): void {
  const cardBody = article.querySelector<HTMLElement>(".msg-card-body");
  if (!cardBody) return;

  // 1. Thinking block
  let thinkingEl = article.querySelector<HTMLElement>(".thinking-box");
  if (stream.thinking) {
    if (!thinkingEl) {
      thinkingEl = node("div");
      thinkingEl.className = "thinking-box";
      thinkingEl.innerHTML = `
        <div class="thinking-header">
          <span class="thinking-header-left">
            <span class="pulse-dot"></span> 思考中 (${stream.thinkingDurationSeconds}s)
          </span>
          <span class="thinking-chevron">▾</span>
        </div>
        <div class="thinking-body"></div>
      `;
      thinkingEl
        .querySelector(".thinking-header")!
        .addEventListener("click", () => {
          const body =
            thinkingEl!.querySelector<HTMLElement>(".thinking-body")!;
          body.hidden = !body.hidden;
        });
      cardBody.insertBefore(thinkingEl, outputPre);
    }
    const headerLeft = thinkingEl.querySelector(".thinking-header-left")!;
    if (stream.isThinking) {
      headerLeft.innerHTML = `<span class="pulse-dot"></span> 思考中 (${stream.thinkingDurationSeconds}s)`;
    } else {
      headerLeft.innerHTML = `🧠 思考过程 (${stream.thinkingDurationSeconds}s)`;
    }
    const body = thinkingEl.querySelector<HTMLElement>(".thinking-body")!;
    body.textContent = stream.thinking;
  }

  // 1.5 Subagents in-flight banner
  let subagentsEl = article.querySelector<HTMLElement>(".subagents-status-bar");
  if (stream.subagents.length > 0) {
    if (!subagentsEl) {
      subagentsEl = node("div");
      subagentsEl.className = "subagents-status-bar";
      cardBody.insertBefore(subagentsEl, outputPre);
    }
    subagentsEl.innerHTML =
      `<span>🤖 子智能体协同:</span> ` +
      stream.subagents
        .map(
          (s) =>
            `<span class="subagent-pill">${escapeHtml(s.name)}: ${escapeHtml(s.status)}</span>`,
        )
        .join(" ");
  }

  // 2. Tools Container
  let toolsContainer = article.querySelector<HTMLElement>(
    ".tool-cards-container",
  );
  if (stream.tools.length > 0) {
    if (!toolsContainer) {
      toolsContainer = node("div");
      toolsContainer.className = "tool-cards-container";
      cardBody.insertBefore(toolsContainer, outputPre);
    }
    toolsContainer.replaceChildren(
      ...stream.tools.map((tool) => renderToolCard(tool)),
    );
  }

  // 3. Approval Card
  let approvalContainer = article.querySelector<HTMLElement>(".approval-card");
  if (stream.pendingApproval) {
    if (!approvalContainer) {
      approvalContainer = node("div");
      approvalContainer.className = "approval-card";
      cardBody.insertBefore(approvalContainer, outputPre);
    }
    const req = stream.pendingApproval;
    approvalContainer.innerHTML = `
      <div class="approval-header">🛡️ 工具安全执行审批请求 (Tool Guard)</div>
      <div class="approval-desc">
        Agent 正在尝试执行工具 <strong>${escapeHtml(req.toolName)}</strong>，当前审批策略要求人工确认授权。
        ${req.reason ? `<br><small style="color:#d46b08;">原因: ${escapeHtml(req.reason)}</small>` : ""}
      </div>
      <div class="approval-actions">
        <button type="button" class="btn-approve">批准单次执行 (Exact)</button>
        <button type="button" class="btn-approve-similar">批准同类执行 (Similar)</button>
        <button type="button" class="btn-deny">拒绝 (Deny)</button>
      </div>
    `;
    approvalContainer
      .querySelector(".btn-approve")!
      .addEventListener("click", () => {
        void resolveApproval(req.requestId, "exact");
      });
    approvalContainer
      .querySelector(".btn-approve-similar")!
      .addEventListener("click", () => {
        void resolveApproval(req.requestId, "similar");
      });
    approvalContainer
      .querySelector(".btn-deny")!
      .addEventListener("click", () => {
        void denyApproval(req.requestId);
      });
  } else if (approvalContainer) {
    approvalContainer.remove();
  }

  // 4. Message text & Markdown
  const existingPreText = outputPre.textContent;
  outputPre.textContent =
    stream.text ||
    (stream.isThinking ? "思考中…" : existingPreText || "收到事件，等待正文…");

  const effectiveText = stream.text || existingPreText;
  let mdBody = article.querySelector<HTMLElement>(".msg-markdown-body");
  if (
    effectiveText &&
    effectiveText !== "思考中…" &&
    effectiveText !== "收到事件，等待正文…"
  ) {
    if (!mdBody) {
      mdBody = node("div");
      mdBody.className = "msg-markdown-body";
      cardBody.insertBefore(mdBody, outputPre);
    }
    mdBody.innerHTML = renderMarkdownToHtml(effectiveText);
    bindMarkdownEvents(mdBody);
    outputPre.classList.add("sr-only");
  } else {
    if (mdBody) mdBody.remove();
    outputPre.classList.remove("sr-only");
  }

  // 5. Turn usage stats
  let usageEl = article.querySelector<HTMLElement>(".msg-footer-stats");
  if (stream.turnUsage) {
    if (!usageEl) {
      usageEl = node("div");
      usageEl.className = "msg-footer-stats";
      usageEl.style.cssText =
        "font-size:11px; color:var(--app-text-tertiary); margin-top:6px; border-top:1px dashed var(--app-border-subtle); padding-top:4px;";
      cardBody.append(usageEl);
    }
    usageEl.textContent = `Token 消耗: 输入 ${stream.turnUsage.prompt_tokens ?? "-"} | 输出 ${stream.turnUsage.completion_tokens ?? "-"} | 总计 ${stream.turnUsage.total_tokens ?? "-"}`;
  }

  // 6. Context Monitor
  contextMonitor.update(stream.turnUsage, stream.contextUsage);
}

async function checkPendingApprovals(): Promise<void> {
  if (!currentChat || !activeStream || activeStream.complete) return;
  try {
    const list = await api.request<{ pending_approvals: RecordValue[] }>(
      query("/approval/list", { session_id: currentChat.session_id }),
    );
    if (list.pending_approvals && list.pending_approvals.length > 0) {
      const p = list.pending_approvals[0];
      if (!activeStream.pendingApproval) {
        activeStream.pendingApproval = {
          requestId: p.request_id,
          sessionId: p.session_id,
          toolName: p.tool_name,
          args: p.arguments,
          reason: p.reasoning || p.result_summary,
        };
        if (activeBubbleEl && activeOutputPre) {
          updateAssistantBubble(activeBubbleEl, activeStream, activeOutputPre);
        }
      }
    }
  } catch {
    // Ignore if approval endpoint not available
  }
}

function updateChatSelection(): void {
  currentChat = chats.find((chat) => chat.id === input("chats"));
  $("chat-identity").textContent = currentChat
    ? `${currentChat.name || "会话 " + currentChat.id.slice(0, 8)} · session ${currentChat.session_id}`
    : "未选择会话";
  $("messages").replaceChildren();
  loadedFile = null;
  renderSidebarSessions();
  void checkpointsPanel.loadCheckpoints();
}

async function refreshChats(selected = currentChat?.id): Promise<void> {
  chats = await api.request<RecordValue[]>("/api/chats?channel=console");
  selectOptions(
    "chats",
    [
      { value: "", label: "选择会话" },
      ...chats.map((chat) => ({ value: chat.id, label: chat.name || chat.id })),
    ],
    selected,
  );
  updateChatSelection();
}

action("refresh-chats", () => refreshChats());
$("chats").addEventListener("change", updateChatSelection);

async function createChat(): Promise<void> {
  const chat = await api.request<RecordValue>("/api/chats", {
    method: "POST",
    body: {
      name: "验证会话 " + new Date().toLocaleTimeString(),
      session_id: crypto.randomUUID(),
      user_id: "default",
      channel: "console",
    },
  });
  chats.unshift(chat);
  selectOptions(
    "chats",
    chats.map((item) => ({ value: item.id, label: item.name || item.id })),
    chat.id,
  );
  updateChatSelection();
}

action("new-chat", createChat, "会话已创建");

action("history", async () => {
  if (!currentChat) throw new Error("请先选择会话");
  const history = await api.request<{ messages: RecordValue[] }>(
    "/api/chats/" + encodeURIComponent(currentChat.id),
  );
  $("messages").replaceChildren();
  for (const item of history.messages) {
    const role = item.role || "message";
    const text = textContent(item.content) || json(item);
    const bubble = message(role, text);
    if (role === "assistant") {
      const stream = new ChatStream();
      stream.consume(item);
      updateAssistantBubble(bubble.article, stream, bubble.pre);
    }
  }
});

action(
  "send-form",
  async () => {
    if (!input("prompt")) throw new Error("请输入消息");
    const timeoutMs = await tl.prepareChat(
      Boolean($<HTMLInputElement>("chat-file").files?.length),
    );
    if (!currentChat) await createChat();
    const chat = currentChat!;
    const text = value("prompt");
    const content: RecordValue[] = [{ type: "text", text }];
    const file = $<HTMLInputElement>("chat-file").files?.[0];
    if (file) {
      const form = new FormData();
      form.append("file", file);
      const uploaded = await api.request<RecordValue>("/api/console/upload", {
        method: "POST",
        body: form,
      });
      content.push({
        type: "file",
        file_url: uploaded.url,
        filename: uploaded.file_name,
      });
    }
    message("user", text + (file ? `\n[附件] ${file.name}` : ""));
    const { article, pre: output } = message("assistant", "等待响应…");
    const stream = new ChatStream();
    activeBubbleEl = article;
    activeOutputPre = output;
    activeStream = stream;

    streamController = new AbortController();
    $<HTMLButtonElement>("stop").disabled = false;
    $("stream-status").textContent = "流式状态：生成中";

    // Start background check for tool approvals
    if (approvalPollTimer) clearInterval(approvalPollTimer);
    approvalPollTimer = setInterval(() => {
      void checkPendingApprovals();
    }, 1200);

    const approvalLevel = input("approval-level") || "SMART";

    try {
      await api.request("/api/console/chat", {
        method: "POST",
        signal: streamController.signal,
        timeoutMs,
        body: {
          input: [{ role: "user", content }],
          session_id: chat.session_id,
          user_id: chat.user_id,
          channel: "console",
          stream: true,
          request_context: {
            approval_level: approvalLevel,
          },
        },
        onEvent: (event) => {
          stream.consume(event);
          updateAssistantBubble(article, stream, output);
        },
      });
      if (stream.error) throw new Error(stream.error);
      if (!stream.complete)
        throw new Error("连接已关闭，但未收到完成事件；可加载历史核对结果");
      updateAssistantBubble(article, stream, output);
      if (!stream.text && !stream.thinking && stream.tools.length === 0) {
        output.textContent = "本轮完成（无文本输出，详见事件日志）";
      }
      $("stream-status").textContent = "流式状态：完成";
      $<HTMLTextAreaElement>("prompt").value = "";
      $<HTMLInputElement>("chat-file").value = "";
      $("chat-file-badge").textContent = "";
      notice("本轮响应完成");
    } catch (error) {
      $("stream-status").textContent = streamController.signal.aborted
        ? "流式状态：已中断，可加载历史核对"
        : "流式状态：异常";
      if (!stream.text && !stream.thinking)
        output.textContent = "未收到正文，请查看请求日志";
      throw error;
    } finally {
      if (approvalPollTimer) {
        clearInterval(approvalPollTimer);
        approvalPollTimer = null;
      }
      streamController = null;
      activeStream = null;
      activeBubbleEl = null;
      activeOutputPre = null;
      $<HTMLButtonElement>("stop").disabled = true;
    }
  },
  "",
);

$("stop").addEventListener("click", () => {
  if (!streamController || !currentChat) return;
  const controller = streamController;
  $<HTMLButtonElement>("stop").disabled = true;
  void api
    .request<{ stopped: boolean }>(
      query("/console/chat/stop", { chat_id: currentChat.id }),
      { method: "POST" },
    )
    .then((result) => {
      if (!result.stopped) throw new Error("后端未停止运行");
      controller.abort();
      notice("后端已接受停止请求");
    })
    .catch((error) => {
      notice(`停止请求失败：${error.message}。生成可能仍在继续。`, true);
      $<HTMLButtonElement>("stop").disabled = false;
    });
});

const managementResult = (data: unknown) => {
  $("management-result").textContent = json(api.safe(data));
};

action("load-models", async () => {
  await tl.load();
  try {
    const active = await api.request<RecordValue>("/api/models/active");
    if (active.model) {
      $("chat-provider").textContent =
        `${active.provider_id || "model"}: ${active.model}`;
    }
  } catch {
    // Ignore
  }
});

action("model-form", async () =>
  managementResult(
    await api.request("/api/models/active", {
      method: "PUT",
      body: {
        provider_id: input("provider-id"),
        model: input("model-id"),
        scope: "agent",
        agent_id: api.agent,
      },
    }),
  ),
);

action("provider-form", async () => {
  managementResult(
    await api.request(
      "/api/models/" +
        encodeURIComponent(input("config-provider-id")) +
        "/config",
      {
        method: "PUT",
        body: {
          base_url: input("provider-url"),
          ...(value("provider-key") ? { api_key: value("provider-key") } : {}),
        },
      },
    ),
  );
  $<HTMLInputElement>("provider-key").value = "";
});

action("load-channels", async () => {
  const channels = await api.request<RecordValue>("/api/config/channels");
  knownChannels = Object.keys(channels);
  managementResult(channels);
  selectOptions(
    "channel-name",
    knownChannels.map((name) => ({ value: name, label: name })),
  );
});

action("read-channel", async () => {
  const name = input("channel-name");
  const config = await api.request(
    "/api/config/channels/" + encodeURIComponent(name),
  );
  $<HTMLTextAreaElement>("channel-json").value = json(config);
  loadedChannel = name;
});

action("channel-form", async () => {
  if (loadedChannel !== input("channel-name"))
    throw new Error("请先读取当前渠道配置");
  const config = JSON.parse(value("channel-json"));
  if (!config || Array.isArray(config) || typeof config !== "object")
    throw new Error("配置必须是 JSON 对象");
  managementResult(
    await api.request(
      "/api/config/channels/" + encodeURIComponent(loadedChannel),
      { method: "PUT", body: config },
    ),
  );
});

async function loadCron(): Promise<void> {
  const jobs = await api.request<RecordValue[]>("/api/cron/jobs");
  $("cron-list").replaceChildren();
  for (const job of jobs) {
    const row = node("div");
    row.className = "list-row";
    row.append(
      node("span", `${job.name} · ${job.enabled ? "运行中" : "暂停"}`),
    );
    const button = node("button", job.enabled ? "暂停" : "恢复");
    button.className = "secondary";
    button.addEventListener(
      "click",
      () =>
        void run(async () => {
          await api.request(
            `/api/cron/jobs/${encodeURIComponent(job.id)}/${job.enabled ? "pause" : "resume"}`,
            { method: "POST" },
          );
          await loadCron();
        }),
    );
    row.append(button);
    $("cron-list").append(row);
  }
  managementResult(jobs);
}

action("load-cron", loadCron);

action(
  "cron-form",
  async () => {
    await api.request("/api/cron/jobs", {
      method: "POST",
      body: {
        id: crypto.randomUUID(),
        name: input("cron-name"),
        enabled: false,
        schedule: {
          type: "cron",
          cron: input("cron-expression"),
          timezone: input("cron-timezone"),
        },
        task_type: "text",
        text: value("cron-text"),
        dispatch: {
          type: "channel",
          channel: "console",
          target: {
            user_id: "default",
            session_id: currentChat?.session_id || "native-validation",
          },
          mode: "final",
        },
      },
    });
    await loadCron();
  },
  "定时任务已创建，保持暂停状态",
);

const fileHeaders = () =>
  currentChat ? { "X-Chat-Id": String(currentChat.id) } : undefined;
const fileRoot = () => input("file-root");

async function browse(append = false): Promise<void> {
  const directory = input("directory");
  if (append && directory !== listedDirectory)
    throw new Error("目录已改变，请重新浏览");
  const page = await api.request<RecordValue>(
    query("/workspace/tree", {
      path: directory,
      root: fileRoot(),
      limit: 100,
      ...(append ? { cursor: nextCursor } : {}),
    }),
    { headers: fileHeaders() },
  );
  if (!append) $("file-list").replaceChildren();
  for (const entry of page.entries) {
    const isDir = entry.kind === "directory";
    const row = node(
      "button",
      `${isDir ? "📁" : "📄"} ${entry.name}${entry.size == null ? "" : ` (${entry.size} B)`}`,
    );
    row.className = "file-entry";
    row.addEventListener(
      "click",
      () =>
        void run(async () => {
          if (entry.kind === "directory") {
            $<HTMLInputElement>("directory").value = entry.path;
            await browse();
          } else {
            $<HTMLInputElement>("file-path").value = entry.path;
            await openFile();
          }
        }),
    );
    $("file-list").append(row);
  }
  listedDirectory = directory;
  nextCursor = page.next_cursor || "";
  $<HTMLButtonElement>("more-files").disabled = !page.has_more || !nextCursor;
}

async function openFile(): Promise<void> {
  const path = input("file-path");
  if (!path) throw new Error("请输入文件路径");
  loadedFile = null;
  let offset = 0;
  let etag = "";
  let content = "";
  let size = 0;
  for (;;) {
    const chunk = await api.request<RecordValue>(
      query("/workspace/file-content", {
        path,
        root: fileRoot(),
        offset,
        limit: 256 * 1024,
      }),
      { headers: fileHeaders() },
    );
    if (etag && etag !== chunk.etag)
      throw new Error("读取期间文件已变化，请重新读取");
    etag = chunk.etag;
    content += chunk.content;
    size += new TextEncoder().encode(chunk.content).byteLength;
    if (size > 2 * 1024 * 1024)
      throw new Error("文本超出 2 MiB 编辑上限，请下载查看");
    if (chunk.eof) break;
    if (!(chunk.next_offset > offset)) throw new Error("文件分页未前进");
    offset = chunk.next_offset;
  }
  if (!etag) throw new Error("后端未返回 ETag，无法安全编辑");
  loadedFile = { path, root: fileRoot(), chatId: currentChat?.id || "", etag };
  $<HTMLTextAreaElement>("file-content").value = content;
  $("file-version").textContent = `${path} · ETag ${etag}`;
}

action("browse-form", () => browse());
action("more-files", () => browse(true));
action("open-form", openFile);

$("file-root").addEventListener("change", () => {
  loadedFile = null;
  nextCursor = "";
  $("file-list").replaceChildren();
  $<HTMLButtonElement>("more-files").disabled = true;
});

action(
  "save-file",
  async () => {
    if (
      !loadedFile ||
      loadedFile.path !== input("file-path") ||
      loadedFile.root !== fileRoot() ||
      loadedFile.chatId !== (currentChat?.id || "")
    )
      throw new Error("请先读取当前路径的文件");
    if (
      new TextEncoder().encode(value("file-content")).byteLength >
      2 * 1024 * 1024
    )
      throw new Error("文本超出 2 MiB 编辑上限");
    const result = await api.request<RecordValue>(
      query("/workspace/file-content", {
        path: loadedFile.path,
        root: loadedFile.root,
      }),
      {
        method: "PUT",
        headers: { ...fileHeaders(), "If-Match": loadedFile.etag },
        body: { content: value("file-content") },
      },
    );
    loadedFile.etag = result.etag;
    $("file-version").textContent = `${loadedFile.path} · ETag ${result.etag}`;
  },
  "文件保存成功",
);

action(
  "upload-form",
  async () => {
    const form = new FormData();
    for (const file of $<HTMLInputElement>("upload-files").files || [])
      form.append("files", file);
    await api.request(
      query("/workspace/file-upload", {
        path: input("directory"),
        root: fileRoot(),
        conflict: "rename",
      }),
      { method: "POST", body: form, headers: fileHeaders() },
    );
    $<HTMLInputElement>("upload-files").value = "";
    await browse();
  },
  "文件上传完成",
);

function download(data: Blob, name: string): void {
  const url = URL.createObjectURL(data);
  const anchor = node("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

action("download-file", async () => {
  const path = input("file-path");
  if (!path) throw new Error("请输入文件路径");
  const blob = await api.request<Blob>(
    query("/workspace/file-download", { path, root: fileRoot() }),
    { headers: fileHeaders(), blob: true },
  );
  download(blob, path.split("/").pop() || "download");
});

let logFrame = 0;
function renderLogs(): void {
  const filter = input("log-filter").toLowerCase();
  const list = $("log-list");
  const opened = new Set(
    [...list.querySelectorAll<HTMLDetailsElement>("details[open]")].map(
      (el) => el.dataset.id,
    ),
  );
  list.replaceChildren();
  $("log-count").textContent = String(api.logs.length);
  for (const log of api.logs) {
    if (
      filter &&
      !`${log.method} ${log.url} ${log.status}`.toLowerCase().includes(filter)
    )
      continue;
    const details = node("details");
    details.dataset.id = String(log.id);
    details.open = opened.has(String(log.id));
    const summary = node(
      "summary",
      `${log.method} ${log.url} · ${log.status} · ${log.duration} ms${log.eventCount ? ` · ${log.eventCount} 事件` : ""}`,
    );
    const pre = node("pre", json(log));
    details.append(summary, pre);
    list.append(details);
  }
}

api.onLog = () => {
  if (!logFrame)
    logFrame = requestAnimationFrame(() => {
      logFrame = 0;
      renderLogs();
    });
};

$("log-filter").addEventListener("input", renderLogs);
$("clear-logs").addEventListener("click", () => api.clear());
$("export-logs").addEventListener("click", () =>
  download(
    new Blob([json(api.logs)], { type: "application/json" }),
    "native-console-logs.json",
  ),
);
