import { ApiClient } from "./api";
import type { RecordValue } from "./chat";

export const TL_FIELDS = [
  ["app_id", "App ID", "", "text"],
  ["tr_code", "交易代码 tr_code", "", "text"],
  ["tr_version", "交易版本 tr_version", "", "text"],
  ["system_prompt_variable_name", "系统提示词变量名", "system_prompt", "text"],
  ["json_correction_max_attempts", "JSON 修正次数（0 或 1）", 1, "number"],
  ["timeout_seconds", "请求超时（秒）", 150, "number"],
  ["stream_idle_timeout_seconds", "流空闲超时（秒，0 为不限）", 0, "number"],
  ["max_request_bytes", "请求上限（字节）", 1048576, "number"],
  ["max_response_bytes", "正文响应上限（字节）", 4194304, "number"],
  ["max_wire_response_bytes", "线路响应上限（字节）", 67108864, "number"],
  ["max_sse_event_bytes", "单个 SSE 事件上限（字节）", 1048576, "number"],
] as const;

export function validateTL(base: string, fields: RecordValue): RecordValue {
  const url = new URL(base);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.search ||
    url.hash ||
    url.username ||
    url.password
  )
    throw new Error(
      "TL Base URL 必须是无查询参数、片段和凭据的 HTTP(S) 服务根地址",
    );
  const config: RecordValue = { tool_calling_mode: "system_prompt" };
  for (const [key, label, , type] of TL_FIELDS) {
    if (type === "text") {
      config[key] = String(fields[key] ?? "");
      if (key === "system_prompt_variable_name" && !config[key].trim())
        throw new Error("系统提示词变量名不能为空");
    } else {
      const n = Number(fields[key]);
      const zeroAllowed =
        key === "json_correction_max_attempts" ||
        key === "stream_idle_timeout_seconds";
      if (
        fields[key] === "" ||
        typeof fields[key] === "boolean" ||
        !Number.isFinite(n) ||
        (zeroAllowed ? n < 0 : n <= 0)
      )
        throw new Error(`${label} 数值无效`);
      if (
        (key.startsWith("max_") || key === "json_correction_max_attempts") &&
        !Number.isSafeInteger(n)
      )
        throw new Error(`${label} 必须是整数`);
      if (key === "json_correction_max_attempts" && n > 1)
        throw new Error("JSON 修正次数只能为 0 或 1");
      config[key] = n;
    }
  }
  return config;
}

type Runner = (task: () => Promise<void>, success?: string) => Promise<void>;
const el = <T extends HTMLElement = HTMLElement>(id: string) =>
  document.getElementById(id) as T;
const val = (id: string) => el<HTMLInputElement>(id).value;

export class TLPanel {
  private providers: RecordValue[] = [];
  private active: RecordValue = {};
  constructor(
    private api: ApiClient,
    private run: Runner,
    private report: (data: unknown) => void,
  ) {
    el("tl-panel").innerHTML =
      `<form id="tl-form" class="subcard"><h3>TL Provider · chatbbc</h3>
      <div class="row"><label>已配置的 TL Provider<select id="tl-provider"><option value="">先读取模型配置</option></select></label><button id="load-tl" type="button" class="secondary">读取 TL 配置</button></div>
      <p id="tl-active" class="hint">浏览器 → QwenPaw /api → TL 服务；不会在浏览器直连 chatbbc。</p>
      <fieldset id="tl-fields" disabled><legend>TL 服务设置</legend>
      <label>TL Base URL<input id="tl-url" type="url" required placeholder="http://127.0.0.1:8089"></label>
      <label>可选 API Key<input id="tl-key" type="password" autocomplete="off" placeholder="留空保留后端原有值"></label>
      <div class="forms-grid">${TL_FIELDS.map(([key, label, , type]) => `<label>${label}<input id="tl-${key}" type="${type}" ${type === "number" ? 'step="any" required' : ""}></label>`).join("")}</div>
      <p class="hint">工具调用模式固定为 system_prompt；每个 TL endpoint 只对应一个模型标签。采样参数和实际模型路由由 TL 服务管理。</p>
      <div class="row"><button id="save-tl">保存 TL 配置</button><button id="test-tl" type="button" class="secondary">测试会话初始化</button></div>
      <div class="row"><label>TL 模型标签<select id="tl-model"></select></label><button id="activate-tl" type="button">用于当前 Agent</button><button id="test-tl-model" type="button" class="secondary">测试聊天（已保存配置）</button></div></fieldset>
      <p id="tl-result" role="status">未加载 TL Provider</p></form>`;
    el("load-tl").addEventListener(
      "click",
      () =>
        void run(async () => {
          await this.load();
        }),
    );
    el("tl-provider").addEventListener("change", () => this.fill());
    el("tl-form").addEventListener("submit", (event) => {
      event.preventDefault();
      void run(() => this.save(), "TL 配置已保存");
    });
    el("test-tl").addEventListener(
      "click",
      () => void run(() => this.test(), "初始化测试完成；完整聊天需单独验证"),
    );
    el("test-tl-model").addEventListener(
      "click",
      () =>
        void run(async () => {
          const provider = this.selected();
          if (!val("tl-model"))
            throw new Error("该 TL Provider 尚未配置模型标签");
          const result = await api.request<RecordValue>(
            `/api/models/${encodeURIComponent(provider.id)}/models/test`,
            {
              method: "POST",
              body: { model_id: val("tl-model") },
              timeoutMs: Math.max(
                60000,
                (Number(provider.tl_config?.timeout_seconds || 150) + 5) * 1000,
              ),
            },
          );
          report(result);
          el("tl-result").textContent =
            `${result.success ? "聊天测试成功" : "聊天测试失败"}：${String(api.safe(result.message || ""))}`;
          if (!result.success)
            throw new Error("TL 聊天测试失败：" + result.message);
        }, "TL 已保存路由的聊天测试完成"),
    );
    el("activate-tl").addEventListener(
      "click",
      () =>
        void run(async () => {
          const provider = this.selected();
          const model = val("tl-model");
          if (!model) throw new Error("该 TL Provider 尚未配置模型标签");
          await this.api.request("/api/models/active", {
            method: "PUT",
            body: {
              provider_id: provider.id,
              model,
              scope: "agent",
              agent_id: this.api.agent,
            },
          });
          await this.load(provider.id);
        }, "当前 Agent 已使用 TL 模型"),
    );
  }
  reset(): void {
    this.providers = [];
    this.active = {};
    el<HTMLSelectElement>("tl-provider").replaceChildren(
      new Option("先读取模型配置", ""),
    );
    el<HTMLFieldSetElement>("tl-fields").disabled = true;
    el<HTMLInputElement>("tl-key").value = "";
    el<HTMLInputElement>("tl-url").value = "";
    for (const [key] of TL_FIELDS) el<HTMLInputElement>("tl-" + key).value = "";
    el<HTMLSelectElement>("tl-model").replaceChildren();
    el("tl-result").textContent = "未加载 TL Provider";
    el("tl-active").textContent = "请读取当前 Agent 的模型配置";
    el("chat-provider").textContent = "发送前自动核对当前 Agent 模型";
  }
  async load(preferred = val("tl-provider")): Promise<void> {
    const providers = await this.api.request<RecordValue[]>("/api/models");
    const active = await this.api.request<RecordValue>(
      `/api/models/active?scope=effective&agent_id=${encodeURIComponent(this.api.agent)}`,
    );
    this.providers = providers;
    this.active = active;
    const tl = providers.filter((item) => item.chat_model === "TLChatModel");
    const select = el<HTMLSelectElement>("tl-provider");
    select.replaceChildren(
      ...tl.map(
        (item) => new Option(`${item.name || item.id} (${item.id})`, item.id),
      ),
    );
    const selected = preferred || active.active_llm?.provider_id;
    if (tl.some((item) => item.id === selected)) select.value = selected;
    const activeProvider = providers.find(
      (item) => item.id === active.active_llm?.provider_id,
    );
    el("chat-provider").textContent = activeProvider
      ? `当前模型：${activeProvider.id} / ${active.active_llm?.model} · ${activeProvider.chat_model === "TLChatModel" ? "TL 仅文本，附件不可用" : activeProvider.chat_model || "常规模型"}`
      : "当前 Agent 尚未设置模型";
    el("tl-active").textContent =
      `当前 Agent：${this.api.agent} · 生效模型：${active.active_llm?.provider_id || "未设置"} / ${active.active_llm?.model || "未设置"}`;
    this.fill();
    this.report({ providers, active });
  }
  private selected(): RecordValue {
    const provider = this.providers.find(
      (item) =>
        item.id === val("tl-provider") && item.chat_model === "TLChatModel",
    );
    if (!provider) throw new Error("请先读取并选择后端已配置的 TL Provider");
    return provider;
  }
  private fill(): void {
    const provider = this.providers.find(
      (item) => item.id === val("tl-provider"),
    );
    el<HTMLFieldSetElement>("tl-fields").disabled = !provider;
    el<HTMLInputElement>("tl-key").value = "";
    if (!provider) {
      el("tl-result").textContent =
        "后端未返回 TL Provider，请先配置 tl-provider.json 再读取。";
      return;
    }
    el<HTMLInputElement>("tl-url").value = provider.base_url || "";
    for (const [key, , fallback] of TL_FIELDS)
      el<HTMLInputElement>("tl-" + key).value = String(
        provider.tl_config?.[key] ?? fallback,
      );
    const removed = new Set(provider.removed_model_ids || []);
    const models: RecordValue[] = [
      ...(provider.models || []),
      ...(provider.extra_models || []),
      ...(provider.discovered_models || []),
    ];
    const ids = [
      ...new Set(
        models
          .filter((item) => !removed.has(item.id))
          .map((item) => String(item.id)),
      ),
    ];
    el<HTMLSelectElement>("tl-model").replaceChildren(
      ...ids.map((id) => new Option(id, id)),
    );
    el("tl-result").textContent =
      `已加载 ${provider.id}。测试使用当前表单值，保存前不会修改后端配置。`;
  }
  private body(): RecordValue {
    this.selected();
    const base_url = val("tl-url").trim();
    const tl_config = validateTL(
      base_url,
      Object.fromEntries(TL_FIELDS.map(([key]) => [key, val("tl-" + key)])),
    );
    return {
      base_url,
      tl_config,
      ...(val("tl-key") ? { api_key: val("tl-key") } : {}),
    };
  }
  private async save(): Promise<void> {
    const provider = this.selected();
    await this.api.request(
      `/api/models/${encodeURIComponent(provider.id)}/config`,
      { method: "PUT", body: this.body() },
    );
    await this.load(provider.id);
    el("tl-result").textContent = "TL 配置已保存并重新读取";
  }
  private async test(): Promise<void> {
    const provider = this.selected();
    const body = this.body();
    const result = await this.api.request<RecordValue>(
      `/api/models/${encodeURIComponent(provider.id)}/test`,
      {
        method: "POST",
        body,
        timeoutMs: Math.max(60000, (body.tl_config.timeout_seconds + 5) * 1000),
      },
    );
    this.report(result);
    el("tl-result").textContent =
      `${result.success ? "初始化成功" : "初始化失败"}：${String(this.api.safe(result.message || ""))}（不代表聊天已验证）`;
    if (!result.success)
      throw new Error("TL 初始化测试失败：" + result.message);
  }
  async prepareChat(hasAttachment: boolean): Promise<number> {
    const providers = await this.api.request<RecordValue[]>("/api/models");
    const active = await this.api.request<RecordValue>(
      `/api/models/active?scope=effective&agent_id=${encodeURIComponent(this.api.agent)}`,
    );
    const provider = providers.find(
      (item) => item.id === active.active_llm?.provider_id,
    );
    el("chat-provider").textContent =
      `当前模型：${active.active_llm?.provider_id || "未设置"} / ${active.active_llm?.model || "未设置"}${provider?.chat_model === "TLChatModel" ? " · TL 仅文本，附件不可用" : ""}`;
    if (provider?.chat_model !== "TLChatModel") return 60000;
    if (hasAttachment)
      throw new Error("TL v1 仅支持文本输入，请移除附件后发送");
    const idle = Number(provider.tl_config?.stream_idle_timeout_seconds ?? 0);
    // Backend owns request timeout; zero means no additional browser idle cutoff.
    return idle === 0 ? 0 : Math.max(1000, idle * 1000 + 5000);
  }
}
