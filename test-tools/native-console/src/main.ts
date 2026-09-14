import "./style.css";
import { ApiClient } from "./api";
import { TLPanel } from "./tl";
import { ChatStream, textContent, type RecordValue } from "./chat";

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
  <header><div><span class="eyebrow">QWENPAW / TEST TOOLS</span><h1>原生 Web 验证台 <small>0.1</small></h1><p>管理配置 · 文件工作区 · 流式聊天</p></div><span class="badge">Vanilla TypeScript · 零第三方运行时</span></header>
  <section class="connection card"><fieldset id="connection-fields"><legend>连接现有后端</legend>
    <form id="connect-form" class="row"><label>访问令牌<input id="token" type="password" autocomplete="off" placeholder="未启用鉴权可留空"></label><button>连接 / 检查</button><label>Agent<select id="agent"><option value="default">default</option></select></label><button id="logout" type="button" class="secondary">清除凭据</button></form>
    <details><summary>使用账号密码登录</summary><form id="login-form" class="row"><label>用户名<input id="username" autocomplete="username" required></label><label>密码<input id="password" type="password" autocomplete="current-password" required></label><button>登录</button></form></details>
  </fieldset><p id="connection-status">开发代理默认连接 http://127.0.0.1:8088；真实请求仅由操作触发。</p></section>
  <div id="notice" role="status" aria-live="polite">就绪。先连接后端，再选择需要验证的流程。</div>
  <main><section class="workspace card"><nav aria-label="功能页"><button data-tab="chat" aria-current="page">核心聊天</button><button data-tab="manage">管理页面</button><button data-tab="files">基础文件</button></nav>
    <fieldset id="work-fields"><legend class="sr-only">功能验证</legend>
    <section id="chat" class="tab"><div class="section-title"><h2>核心聊天</h2><button id="refresh-chats" class="secondary">刷新会话</button></div>
      <div class="row"><label>会话<select id="chats"><option value="">选择会话</option></select></label><button id="new-chat">新建会话</button><button id="history" class="secondary">加载历史</button></div>
      <p id="chat-provider" class="hint">发送前自动核对当前 Agent 模型</p><p id="chat-identity" class="muted">未选择会话</p><div id="messages" class="messages" role="log" aria-label="聊天消息"></div>
      <form id="send-form"><label>消息<textarea id="prompt" rows="3" placeholder="输入消息，验证真实流式响应" required></textarea></label><div class="row"><label>附件<input id="chat-file" type="file"></label><button id="send">发送</button></div></form>
      <p class="hint">正文以安全纯文本展示；工具事件可在请求日志展开查看。断流后可加载历史恢复已保存结果。</p>
    </section>
    <section id="manage" class="tab" hidden><h2>管理页面</h2><div class="row"><button id="load-models">读取模型配置</button><button id="load-channels" class="secondary">读取渠道列表</button><button id="load-cron" class="secondary">读取定时任务</button></div>
      <pre id="management-result" class="result">尚未读取配置</pre>
      <div class="forms-grid"><form id="model-form" class="subcard"><h3>设置 Agent 模型</h3><label>Provider ID<input id="provider-id" required placeholder="例如 deepseek"></label><label>Model ID<input id="model-id" required placeholder="例如 deepseek-chat"></label><button>保存模型选择</button></form>
      <form id="provider-form" class="subcard"><h3>Provider 连接配置</h3><label>Provider ID<input id="config-provider-id" required></label><label>Base URL<input id="provider-url" type="url" required></label><label>API Key<input id="provider-key" type="password" autocomplete="off" placeholder="留空时不提交此字段"></label><button>保存 Provider 配置</button></form></div>
      <div id="tl-panel"></div>
      <form id="channel-form" class="subcard"><h3>渠道配置</h3><div class="row"><label>渠道<select id="channel-name"><option value="console">console</option></select></label><button id="read-channel" type="button" class="secondary">读取该渠道</button></div><label>配置 JSON<textarea id="channel-json" rows="7" spellcheck="false" placeholder="先读取渠道配置，再编辑"></textarea></label><button>保存渠道配置</button></form>
      <div class="subcard"><h3>定时任务</h3><div id="cron-list"></div><form id="cron-form"><div class="row"><label>任务名称<input id="cron-name" required></label><label>Cron 表达式<input id="cron-expression" value="0 9 * * *" required></label><label>时区<input id="cron-timezone" value="Asia/Shanghai" required></label></div><label>通知文本<input id="cron-text" required></label><button>创建（默认暂停）</button></form></div>
    </section>
    <section id="files" class="tab" hidden><div class="section-title"><h2>基础文件</h2><label>文件根目录<select id="file-root"><option value="workspace">Agent workspace</option><option value="project">当前会话 project</option></select></label></div>
      <form id="browse-form" class="row"><label>目录路径<input id="directory" placeholder="空值为根目录"></label><button>浏览目录</button><button type="button" id="more-files" disabled class="secondary">下一页</button></form><div id="file-list" class="file-list"></div>
      <form id="open-form" class="row"><label>文件路径<input id="file-path" placeholder="从列表选择或输入相对路径" required></label><button>读取文件</button></form><p id="file-version" class="muted">未读取文件；保存使用 ETag 防止覆盖其他修改。</p><label>文本内容<textarea id="file-content" rows="14" spellcheck="false"></textarea></label><div class="row"><button id="save-file">保存文件</button><button id="download-file" class="secondary">下载文件</button></div>
      <form id="upload-form" class="subcard"><label>上传到当前目录<input id="upload-files" type="file" multiple required></label><p class="hint">同名文件采用 rename 策略。文本编辑上限 2 MiB；二进制文件使用下载。</p><button>上传文件</button></form>
    </section></fieldset>
    <div class="stream-controls"><span id="stream-status">流式状态：空闲</span><button id="stop" disabled class="danger">停止生成</button></div>
  </section>
  <aside class="logs card"><div class="section-title"><h2>请求接口日志 <small id="log-count">0</small></h2><div><button id="export-logs" class="secondary">导出</button><button id="clear-logs" class="secondary">清空</button></div></div><label>筛选接口<input id="log-filter" placeholder="例如 /console/chat 或 401"></label><p class="hint">最近 100 个请求 · 每条保留最后 100 个事件 · 密码/令牌字段脱敏 · 仅保存在内存</p><div id="log-list"></div></aside></main>
  <footer>独立验证包，不加载现有 console 或插件代码。配置保存和聊天发送会调用所连接后端。</footer>`;

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
function resetContext(): void {
  tl.reset();
  chats = [];
  currentChat = undefined;
  loadedFile = null;
  loadedChannel = "";
  nextCursor = "";
  knownChannels = [];
  $("messages").replaceChildren();
  $("file-list").replaceChildren();
  $("cron-list").replaceChildren();
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
    agents.map((agent) => ({ value: agent.id, label: agent.name || agent.id })),
    api.agent,
  );
  api.agent = input("agent") || "default";
  await tl.load();
  $("connection-status").textContent =
    `已连接 · ${status.enabled ? "鉴权已验证" : "未启用鉴权"} · Agent: ${api.agent}`;
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
});
$("agent").addEventListener("change", () => {
  api.agent = input("agent");
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
  }),
);

function message(role: string, text: string): HTMLElement {
  const article = node("article");
  article.className = "message " + (role === "user" ? "user" : "assistant");
  article.append(node("strong", role), node("pre", text));
  $("messages").append(article);
  article.scrollIntoView({ block: "nearest" });
  return article.querySelector("pre")!;
}
function updateChatSelection(): void {
  currentChat = chats.find((chat) => chat.id === input("chats"));
  $("chat-identity").textContent = currentChat
    ? `会话 ${currentChat.id} · session ${currentChat.session_id}`
    : "未选择会话";
  $("messages").replaceChildren();
  loadedFile = null;
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
      name: "原生 Web 验证 " + new Date().toLocaleTimeString(),
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
  for (const item of history.messages)
    message(item.role || "message", textContent(item.content) || json(item));
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
    const output = message("assistant", "等待响应…");
    const stream = new ChatStream();
    streamController = new AbortController();
    $<HTMLButtonElement>("stop").disabled = false;
    $("stream-status").textContent = "流式状态：生成中";
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
        },
        onEvent: (event) => {
          stream.consume(event);
          output.textContent = stream.text || "收到事件，等待正文…";
        },
      });
      if (stream.error) throw new Error(stream.error);
      if (!stream.complete)
        throw new Error("连接已关闭，但未收到完成事件；可加载历史核对结果");
      output.textContent =
        stream.text || "本轮完成（无文本输出，详见事件日志）";
      $("stream-status").textContent = "流式状态：完成";
      $<HTMLTextAreaElement>("prompt").value = "";
      $<HTMLInputElement>("chat-file").value = "";
      notice("本轮响应完成");
    } catch (error) {
      $("stream-status").textContent = streamController.signal.aborted
        ? "流式状态：已中断，可加载历史核对"
        : "流式状态：异常";
      if (!stream.text) output.textContent = "未收到正文，请查看请求日志";
      throw error;
    } finally {
      streamController = null;
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
action("load-models", () => tl.load());
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
    const row = node(
      "button",
      `${entry.kind === "directory" ? "▸" : "·"} ${entry.name}${entry.size == null ? "" : ` (${entry.size} B)`}`,
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
  // Logged download support is implemented by the shared transport's blob mode.
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
