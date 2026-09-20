// Connection, authentication, and chat-mode switching.
//
// Local mode verifies `/api/auth/status`, then loads the Agent list and TL
// config. Service mode checks the `/v1` health and assistant metadata instead.
// The bearer token stays server-side in service mode: the Vite proxy injects it.

import type { ApiClient } from "../api";
import type { RecordValue } from "../chat";
import type { ContextMonitor } from "../context-monitor";
import type { InboxPanel } from "../inbox";
import type { ServiceClient } from "../service";
import type { SkillsPanel } from "../skills";
import type { TLPanel } from "../tl";
import { action, chatMode, notice, run, type ChatMode } from "../core/app";
import { $, input, selectOptions, value } from "../core/dom";
import { resetSelections, state } from "../core/state";

/** Build-time constants injected by vite.config.ts `define`. */
declare const __QWENPAW_SERVICE_USER__: string;
declare const __QWENPAW_CHAT_MODE__: string;
declare const __QWENPAW_MODEL_DEBUG_DEFAULT__: boolean;

export const serviceUser =
  typeof __QWENPAW_SERVICE_USER__ === "string"
    ? __QWENPAW_SERVICE_USER__
    : "default";

export const modelDebugDefault =
  typeof __QWENPAW_MODEL_DEBUG_DEFAULT__ !== "undefined" &&
  __QWENPAW_MODEL_DEBUG_DEFAULT__ === true;

export function bindConnect(deps: {
  api: ApiClient;
  service: ServiceClient;
  tl: TLPanel;
  contextMonitor: ContextMonitor;
  inboxPanel: InboxPanel;
  skillsPanel: SkillsPanel;
  clearModelLogs: () => void;
  refreshServiceSessions: (selected?: string) => Promise<void>;
}): {
  connect(): Promise<void>;
  setChatModeUi(mode: ChatMode): void;
  resetContext(): void;
} {
  const {
    api,
    service,
    tl,
    contextMonitor,
    inboxPanel,
    skillsPanel,
    clearModelLogs,
    refreshServiceSessions,
  } = deps;

  function resetContext(): void {
    state.streamController?.abort();
    state.activeServiceRunId = "";
    state.serviceCancelRequested = false;
    clearModelLogs();
    tl.reset();
    contextMonitor.update(null, null);
    resetSelections();
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

  function setChatModeUi(mode: ChatMode): void {
    const serviceMode = mode === "service";
    document.body.dataset.chatMode = mode;
    $("service-tl-panel").hidden = !serviceMode;
    $("tl-panel").hidden = serviceMode;
    $("service-tl-model").textContent = "连接服务后读取平台模型与配置版本。";
    $<HTMLInputElement>("chat-file").disabled = serviceMode;
    $<HTMLInputElement>("chat-file").value = "";
    $("chat-file-badge").textContent = "";
    $<HTMLSelectElement>("agent").disabled = serviceMode;
    $<HTMLSelectElement>("approval-level").disabled = serviceMode;
    $("approval-level").title = serviceMode
      ? "审批策略由服务端平台配置决定"
      : "本地工具审批策略";
    if (serviceMode) $("chat-provider").textContent = "服务模型：连接后获取";
    $("chat-mode-badge").textContent = serviceMode
      ? "服务聊天 /v1"
      : "本地 Console";
    $("service-identity").textContent = serviceMode
      ? `开发用户: ${serviceUser} · token 仅由 Vite 代理注入`
      : "管理请求使用当前页面的本地凭据";
    $("connection-status").textContent = serviceMode
      ? "服务模式使用 /api/service → /v1；管理页仍连接本地 /api。"
      : "开发代理默认连接 http://127.0.0.1:8088；真实请求仅由操作触发。";
    $<HTMLInputElement>("token").disabled = serviceMode;
    $<HTMLInputElement>("username").disabled = serviceMode;
    $<HTMLInputElement>("password").disabled = serviceMode;
    $<HTMLButtonElement>("logout").disabled = serviceMode;
    $("agent-type-pill").textContent = serviceMode ? "Service" : "Active";
  }

  async function connectService(): Promise<void> {
    resetContext();
    let health: Record<string, unknown>;
    try {
      health = await service.health();
    } catch (error) {
      throw new Error(
        `多用户服务连接失败。请运行 npm run service:tl，并确认 QWENPAW_SERVICE_TARGET 指向 8092 的 /v1 服务；8090 可能是 office 服务。原始错误：${String(error)}`,
      );
    }
    if (health.mode !== undefined && health.mode !== "server")
      throw new Error("代理目标不是 QwenPaw 多用户服务");
    try {
      const assistant = await service.assistant();
      $<HTMLInputElement>("chat-file").disabled =
        assistant.attachments !== true;
      $("chat-file-badge").textContent =
        assistant.attachments === true ? "" : "当前服务未启用附件存储";
      $("service-tl-model").textContent =
        `平台模型：${String(assistant.model || "未知")} · 协议：${String(assistant.model_protocol || "未知")} · 配置版本：${String(assistant.version || "未知")}`;
      const model = assistant.model || assistant.name;
      if (model) $("chat-provider").textContent = `服务: ${String(model)}`;
    } catch {
      // Older service deployments may not expose the optional metadata route.
    }
    await refreshServiceSessions();
    $("connection-status").textContent =
      `服务已连接 · ${String(health.status || "ok")} · 用户 ${serviceUser}`;
    $("conn-text").textContent = `服务已连接 · ${serviceUser}`;
    $("conn-dot").className = "status-dot connected";
  }

  async function connect(): Promise<void> {
    if (chatMode() === "service") {
      await connectService();
      return;
    }
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

  // Seed the mode from the build-time default before any wiring runs.
  if (
    typeof __QWENPAW_CHAT_MODE__ !== "undefined" &&
    __QWENPAW_CHAT_MODE__ === "service"
  )
    $<HTMLSelectElement>("chat-mode").value = "service";

  $<HTMLSelectElement>("chat-mode").addEventListener("change", () => {
    resetContext();
    setChatModeUi(chatMode());
    notice(
      chatMode() === "service"
        ? "已切换服务聊天模式，请连接服务"
        : "已切换本地 Console 模式，请连接本地后端",
    );
  });

  // Toggle Connection Panel
  $("toggle-connect-panel").addEventListener("click", () => {
    const panel = $("connection-panel");
    panel.hidden = !panel.hidden;
  });

  return { connect, setChatModeUi, resetContext };
}
