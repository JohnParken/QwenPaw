// App shell: sidebar, header, connection panel, and the two log drawers.
//
// Extracted verbatim from the original inline template in main.ts. Element ids,
// class names, and copy are intentionally unchanged: the Vitest and Playwright
// suites select on them.

import { chatView } from "./views/chat";
import { filesView } from "./views/files";
import { inboxView } from "./views/inbox";
import { managementView } from "./views/management";
import { skillsView } from "./views/skills";

const sidebar = `
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
`;

const headerAndConnection = `
    <!-- Top Header -->
    <header class="app-header">
      <div class="header-left">
          <span id="chat-identity" class="current-chat-name">未选择会话</span>
          <span id="chat-provider" class="header-tag accent">TL: deepseek-v4-flash</span>
          <span id="active-agent-badge" class="header-tag">Agent: default</span>
          <span id="chat-mode-badge" class="header-tag service-mode-badge">本地 Console</span>
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
        <label class="debug-toggle" title="为下一次模型请求启用后端诊断，同时打开前端传输调试日志">
          <input id="debug-toggle" data-testid="model-debug" type="checkbox">
          Debug
        </label>
        <button id="toggle-logs-btn" class="header-btn" type="button">
          <span>📋 协议日志</span>
          <span id="log-count" class="badge-count">0</span>
        </button>
        <button id="toggle-model-logs-btn" class="header-btn" type="button">
          <span>🧠 模型诊断</span>
          <span id="model-log-count" class="badge-count">0</span>
        </button>
      </div>
    </header>

    <!-- Connection & Auth Settings (Card at top, easily accessible) -->
    <section id="connection-panel" class="connection card connection-panel-card">
      <fieldset id="connection-fields" class="fieldset-reset">
        <legend class="sr-only">连接现有后端</legend>
        <div class="connection-head">
          <span class="connection-heading">🔗 后端连接与凭据</span>
          <p id="connection-status" class="connection-status-text">开发代理默认连接 http://127.0.0.1:8088；真实请求仅由操作触发。</p>
        </div>
        <div class="connection-row">
          <label class="inline-field-head">聊天模式
            <select id="chat-mode" class="select-compact">
              <option value="local" selected>本地 Console 管理</option>
              <option value="service">服务聊天 /v1</option>
            </select>
          </label>
          <span id="service-identity" class="muted text-12">服务用户由开发服务器固定注入</span>
          <form id="connect-form" class="row inline-flex-gap-8">
            <label class="inline-field">访问令牌<input id="token" type="password" autocomplete="off" placeholder="未启用鉴权可留空" class="input-token"></label>
            <button class="primary btn-compact">连接 / 检查</button>
            <button id="logout" type="button" class="secondary btn-compact-tight">清除凭据</button>
          </form>
          <details class="auth-details">
            <summary class="auth-summary">使用账号密码登录</summary>
            <form id="login-form" class="row inline-flex-gap-8-top-4">
              <label class="text-12">用户名<input id="username" autocomplete="username" required class="input-user"></label>
              <label class="text-12">密码<input id="password" type="password" autocomplete="current-password" required class="input-pass"></label>
              <button class="primary btn-compact-xs">登录</button>
            </form>
          </details>
        </div>
      </fieldset>
    </section>

    <!-- Notification Banner -->
    <div id="notice" role="status" aria-live="polite">就绪。先连接后端，再选择需要验证的流程。</div>
`;

const drawers = `
      <!-- Protocol & SSE Logs Drawer (Side Panel) -->
      <aside id="logs-drawer" class="logs-drawer">
        <div class="logs-drawer-header">
          <h2>📋 接口与 SSE 请求日志</h2>
          <div class="flex-gap-6">
            <button id="export-logs" class="secondary btn-compact-11">导出</button>
            <button id="clear-logs" class="secondary btn-compact-11">清空</button>
            <button id="close-logs-btn" class="secondary btn-compact-11">✕</button>
          </div>
        </div>
        <div class="logs-drawer-search">
          <input id="log-filter" placeholder="筛选接口，例如 /console/chat 或 401">
        </div>
        <div id="log-list" class="logs-drawer-list"></div>
      </aside>
      <aside id="model-logs-drawer" class="model-logs-drawer closed" aria-label="模型诊断日志">
        <div class="logs-drawer-header">
          <h2>🧠 模型诊断</h2>
          <div class="flex-gap-6">
            <button id="export-model-logs" class="secondary btn-compact-11">导出</button>
            <button id="clear-model-logs" class="secondary btn-compact-11">清空</button>
            <button id="close-model-logs-btn" class="secondary btn-compact-11">✕</button>
          </div>
        </div>
        <div class="logs-drawer-search">
          <input id="model-log-filter" placeholder="筛选 request、response、tool 或 error">
        </div>
        <p id="model-log-empty" class="model-log-empty">暂无模型诊断事件。开启 Debug 后发送一条消息；后端还需 QWENPAW_MODEL_DEBUG=1。</p>
        <div id="model-log-list" class="logs-drawer-list"></div>
      </aside>
`;

/** Full application markup, assembled from the shell plus one module per view. */
export const appShell = `${sidebar}
  <!-- Main Content Layout -->
  <div class="app-main-content">
${headerAndConnection}
    <!-- Main Views Body -->
    <div class="app-view-body">
      <fieldset id="work-fields" class="fieldset-reset-contents">
        <legend class="sr-only">功能验证</legend>

${chatView}

${filesView}

${inboxView}

${skillsView}

${managementView}
      </fieldset>

${drawers}
    </div>
  </div>
`;
