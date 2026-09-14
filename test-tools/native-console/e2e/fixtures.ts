import { test as base, type Route } from "@playwright/test";

export interface MockCall {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
}

export interface MockState {
  calls: MockCall[];
  authEnabled: boolean;
  force401: boolean;
  delayChat: number;
  agents: Array<Record<string, unknown>>;
  providers: Array<Record<string, unknown>>;
  active: { active_llm: { provider_id: string; model: string } | null };
  channels: Record<string, Record<string, unknown>>;
  jobs: Array<Record<string, unknown>>;
  chats: Array<Record<string, unknown>>;
  fileConflict: boolean;
  fileContent: string;
  etag: string;
  tlModelFailNext: boolean;
}

export function createMockState(): MockState {
  return {
    calls: [],
    authEnabled: false,
    force401: false,
    delayChat: 0,
    agents: [
      { id: "default", name: "Default", available_in_chat: true },
      { id: "agent-beta", name: "Beta", available_in_chat: true },
    ],
    providers: [
      {
        id: "demo",
        name: "<img src=x onerror=alert(1)>",
        api_key_prefix: "",
        chat_model: "OpenAIChatModel",
        models: [],
        extra_models: [],
        is_custom: false,
        is_local: true,
        support_model_discovery: false,
        support_connection_check: false,
        freeze_url: false,
        require_api_key: false,
        api_key: "",
        base_url: "http://mock",
        generate_kwargs: {},
      },
      {
        id: "tlproxy",
        name: "TL Proxy",
        api_key_prefix: "",
        chat_model: "TLChatModel",
        models: [
          {
            id: "deepseek-v4-flash",
            name: "deepseek-v4-flash",
            max_input_length: 1048576,
            max_input_length_configured: true,
          },
        ],
        extra_models: [],
        discovered_models: [],
        is_custom: true,
        is_local: false,
        support_model_discovery: false,
        support_connection_check: true,
        freeze_url: false,
        require_api_key: false,
        api_key: "",
        base_url: "http://127.0.0.1:8089",
        generate_kwargs: {},
        tl_config: {
          app_id: "",
          tr_code: "",
          tr_version: "",
          system_prompt_variable_name: "system_prompt",
          tool_calling_mode: "system_prompt",
          json_correction_max_attempts: 1,
          timeout_seconds: 150,
          stream_idle_timeout_seconds: 0,
          max_request_bytes: 1048576,
          max_response_bytes: 4194304,
          max_wire_response_bytes: 67108864,
          max_sse_event_bytes: 1048576,
        },
      },
    ],
    active: { active_llm: { provider_id: "demo", model: "demo-model" } },
    channels: {
      console: { enabled: true, bot_prefix: "", show_thinking: false },
    },
    jobs: [],
    chats: [],
    fileConflict: false,
    fileContent: "original text\n",
    etag: "etag-1",
    tlModelFailNext: false,
  };
}

function bodyOf(route: Route): unknown {
  const raw = route.request().postData();
  if (!raw) return undefined;
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

async function json(route: Route, data: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(data),
  });
}

async function mockApi(route: Route, state: MockState): Promise<void> {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  const method = request.method();
  const body = bodyOf(route);
  state.calls.push({ method, path, headers: request.headers(), body });
  const fail = () =>
    json(route, { detail: "Unauthorized <img src=x onerror=alert(1)>" }, 401);

  if (
    state.force401 &&
    (path === "/api/auth/verify" ||
      path === "/api/auth/login" ||
      (path.startsWith("/api/models/") && method === "PUT"))
  )
    return fail();
  if (path === "/api/auth/status")
    return json(route, {
      enabled: state.authEnabled,
      has_users: state.authEnabled,
    });
  if (path === "/api/auth/verify")
    return json(route, { valid: true, username: "tester" });
  if (path === "/api/auth/login" && method === "POST")
    return json(route, { token: "mock-login-token", username: "tester" });
  if (path === "/api/agents") return json(route, { agents: state.agents });

  if (path === "/api/models" && method === "GET")
    return json(route, state.providers);
  if (path === "/api/models/active" && method === "GET")
    return json(route, state.active);
  if (path === "/api/models/active" && method === "PUT") {
    state.active = {
      active_llm: {
        provider_id: String((body as any)?.provider_id),
        model: String((body as any)?.model),
      },
    };
    return json(route, state.active);
  }
  const providerTest = path.match(/^\/api\/models\/([^/]+)\/test$/);
  if (providerTest && method === "POST") {
    return json(route, {
      success: true,
      message: "Session initialization successful; chat was not tested.",
      verification: "provider_only",
    });
  }
  const modelTest = path.match(/^\/api\/models\/([^/]+)\/models\/test$/);
  if (modelTest && method === "POST") {
    if (state.tlModelFailNext) {
      state.tlModelFailNext = false;
      return json(route, {
        success: false,
        message: "Configured TL route failed.",
        verification: "unverified",
      });
    }
    return json(route, {
      success: true,
      message:
        "Configured TL route completed chat; upstream identity is not reported.",
      verification: "live",
    });
  }
  const providerConfig = path.match(/^\/api\/models\/([^/]+)\/config$/);
  if (providerConfig && method === "PUT") {
    const provider = state.providers.find(
      (item) => item.id === decodeURIComponent(providerConfig[1]),
    );
    if (!provider) return json(route, { detail: "Provider not found" }, 404);
    const patch = (body || {}) as Record<string, unknown>;
    for (const key of [
      "base_url",
      "api_key",
      "chat_model",
      "name",
      "custom_headers",
      "auth_mode",
    ])
      if (key in patch) provider[key] = patch[key];
    if (patch.tl_config) provider.tl_config = patch.tl_config;
    return json(route, provider);
  }

  if (path === "/api/config/channels" && method === "GET")
    return json(route, state.channels);
  const channel = path.match(/^\/api\/config\/channels\/([^/]+)$/)?.[1];
  if (channel && method === "GET")
    return json(
      route,
      state.channels[decodeURIComponent(channel)] || {
        enabled: false,
        bot_prefix: "",
      },
    );
  if (channel && method === "PUT") {
    state.channels[decodeURIComponent(channel)] = (body || {}) as Record<
      string,
      unknown
    >;
    return json(route, state.channels[decodeURIComponent(channel)]);
  }

  if (path === "/api/cron/jobs" && method === "GET")
    return json(route, state.jobs);
  if (path === "/api/cron/jobs" && method === "POST") {
    const job = {
      ...(body as Record<string, unknown>),
      id: `job-${state.jobs.length + 1}`,
    };
    state.jobs.push(job);
    return json(route, job);
  }
  const cron = path.match(/^\/api\/cron\/jobs\/([^/]+)\/(pause|resume)$/);
  if (cron && method === "POST") {
    const job = state.jobs.find(
      (item) => item.id === decodeURIComponent(cron[1]),
    );
    if (job) job.enabled = cron[2] === "resume";
    return json(
      route,
      cron[2] === "pause" ? { paused: true } : { resumed: true },
    );
  }

  if (path === "/api/chats" && method === "GET")
    return json(route, state.chats);
  if (path === "/api/chats" && method === "POST") {
    const requestBody = body as Record<string, unknown>;
    const chat = {
      id: `chat-${state.chats.length + 1}`,
      name: requestBody.name || "New Chat",
      session_id: requestBody.session_id,
      user_id: requestBody.user_id,
      channel: requestBody.channel || "console",
      status: "idle",
    };
    state.chats.unshift(chat);
    return json(route, chat);
  }
  const chatId = path.match(/^\/api\/chats\/([^/]+)$/)?.[1];
  if (chatId && method === "GET")
    return json(route, {
      messages: [
        {
          role: "assistant",
          content: [{ type: "text", text: "history restored" }],
        },
      ],
      status: "idle",
    });
  if (path === "/api/console/upload" && method === "POST")
    return json(route, {
      url: "media/attachment.txt",
      file_name: "attachment.txt",
      size: 4,
    });
  if (path === "/api/console/chat/stop" && method === "POST")
    return json(route, { stopped: true });
  if (path === "/api/console/chat" && method === "POST") {
    if (state.delayChat)
      await new Promise((resolve) => setTimeout(resolve, state.delayChat));
    const events = [
      {
        object: "content",
        type: "text",
        delta: true,
        index: 0,
        msg_id: "m1",
        text: "Hello ",
      },
      {
        object: "tool",
        type: "unknown_tool_event",
        payload: "tool-event-kept",
      },
      {
        object: "content",
        type: "text",
        delta: true,
        index: 0,
        msg_id: "m1",
        text: "world",
      },
      {
        object: "message",
        id: "m1",
        role: "assistant",
        content: [{ type: "text", text: "Hello world" }],
        status: "completed",
      },
      { object: "response", status: "completed", output: [] },
    ];
    const sse = events
      .map((event) => `data: ${JSON.stringify(event)}\n\n`)
      .join("");
    return route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: sse,
    });
  }

  if (path === "/api/workspace/tree" && method === "GET")
    return json(route, {
      entries: [
        {
          path: "notes.txt",
          name: "notes.txt",
          kind: "file",
          size: state.fileContent.length,
        },
      ],
      next_cursor: "",
      has_more: false,
    });
  if (path === "/api/workspace/file-content" && method === "GET")
    return json(route, {
      path: "notes.txt",
      content: state.fileContent,
      offset: 0,
      limit: 262144,
      next_offset: state.fileContent.length,
      eof: true,
      truncated: false,
      encoding: "utf-8",
      etag: state.etag,
    });
  if (path === "/api/workspace/file-content" && method === "PUT") {
    if (state.fileConflict) {
      state.fileConflict = false;
      return json(route, { detail: "File changed on disk" }, 409);
    }
    state.fileContent = String((body as any)?.content || "");
    state.etag = `etag-${Date.now()}`;
    return json(route, {
      path: "notes.txt",
      size: state.fileContent.length,
      etag: state.etag,
    });
  }
  if (path === "/api/workspace/file-upload" && method === "POST")
    return json(route, {
      files: [
        { name: "new.txt", path: "new.txt", size: 3, status: "uploaded" },
      ],
    });
  if (path === "/api/workspace/file-download" && method === "GET")
    return route.fulfill({
      status: 200,
      contentType: "application/octet-stream",
      body: "download-body",
    });

  // Deliberately do not continue unknown API requests to the network.
  return json(route, { detail: `Unmocked API route: ${method} ${path}` }, 404);
}

export const test = base.extend<{ mock: MockState }>({
  mock: async ({ page }, use) => {
    const state = createMockState();
    await page.route("**/api/**", (route) => mockApi(route, state));
    await use(state);
    await page.unroute("**/api/**");
  },
});
