import { readFile } from "node:fs/promises";
import { expect } from "@playwright/test";
import { test, type MockState } from "./fixtures";

async function connect(
  page: import("@playwright/test").Page,
  token = "",
): Promise<void> {
  await page.locator("#token").fill(token);
  await page.locator("#connect-form button").first().click();
  await expect(page.locator("#connection-status")).toContainText("已连接");
}

function calls(mock: MockState, method: string, path: string) {
  return mock.calls.filter(
    (call) => call.method === method && call.path === path,
  );
}

test("连接、Agent、模型、渠道和 Cron 使用真实方法与请求字段", async ({
  page,
  mock,
}) => {
  await page.goto("/");
  await page.getByText("使用账号密码登录", { exact: true }).click();
  await page.locator("#username").fill("tester");
  await page.locator("#password").fill("login-password");
  await page.locator("#login-form button").click();
  await expect(page.locator("#connection-status")).toContainText("已连接");
  await expect(page.locator("#agent")).toHaveValue("default");
  await page.locator("#agent").selectOption("agent-beta");
  await page.locator('button[data-tab="manage"]').click();

  await page.locator("#load-models").click();
  await expect(page.locator("#management-result")).toContainText("demo");
  await page.locator("#provider-id").fill("demo");
  await page.locator("#model-id").fill("demo-v2");
  await page.locator("#model-form button").click();
  await page.locator("#load-channels").click();
  await page.locator("#read-channel").click();
  await page
    .locator("#channel-json")
    .fill(JSON.stringify({ enabled: false, bot_prefix: "changed" }));
  await page.locator("#channel-form button:not(#read-channel)").click();
  await page.locator("#load-cron").click();
  await page.locator("#cron-name").fill("nightly");
  await page.locator("#cron-text").fill("hello from cron");
  await page.locator("#cron-form button").click();
  await expect(page.locator("#cron-list")).toContainText("暂停");
  await page.locator("#cron-list button").click();
  await expect(page.locator("#cron-list")).toContainText("运行中");
  await page.locator("#cron-list button").click();
  await expect(page.locator("#cron-list")).toContainText("暂停");

  const model = calls(mock, "PUT", "/api/models/active").at(-1)!;
  expect(model.body).toMatchObject({
    provider_id: "demo",
    model: "demo-v2",
    scope: "agent",
    agent_id: "agent-beta",
  });
  expect(model.headers["x-agent-id"]).toBe("agent-beta");
  expect(model.headers.authorization).toBe("Bearer mock-login-token");
  expect(
    calls(mock, "PUT", "/api/config/channels/console").at(-1)?.body,
  ).toEqual({ enabled: false, bot_prefix: "changed" });
  const cron = calls(mock, "POST", "/api/cron/jobs").at(-1)!;
  expect(cron.body).toMatchObject({
    name: "nightly",
    task_type: "text",
    text: "hello from cron",
    enabled: false,
  });
  expect(calls(mock, "POST", "/api/cron/jobs/job-1/resume")).toHaveLength(1);
  expect(calls(mock, "POST", "/api/cron/jobs/job-1/pause")).toHaveLength(1);
});

test("文件浏览、ETag 冲突和 multipart 上传保持编辑内容", async ({
  page,
  mock,
}) => {
  await page.goto("/");
  await connect(page);
  await page.locator('button[data-tab="files"]').click();
  await page.locator("#browse-form button").first().click();
  await expect(page.locator("#file-list")).toContainText("notes.txt");
  await page.locator("#file-path").fill("notes.txt");
  await page.locator("#open-form button").click();
  await expect(page.locator("#file-content")).toHaveValue("original text\n");
  await expect(page.locator("#file-version")).toContainText("ETag etag-1");

  await page.locator("#file-content").fill("saved text");
  await page.locator("#save-file").click();
  await expect(page.locator("#notice")).toContainText("文件保存成功");
  const savedEtag = mock.etag;
  await expect(page.locator("#file-version")).toContainText(savedEtag);
  await page.locator("#open-form button").click();
  await expect(page.locator("#file-content")).toHaveValue("saved text");

  mock.fileConflict = true;
  await page.locator("#file-content").fill("local edited text");
  await page.locator("#save-file").click();
  await expect(page.locator("#notice")).toContainText("409");
  await expect(page.locator("#file-content")).toHaveValue("local edited text");
  const downloadPromise = page.waitForEvent("download");
  await page.locator("#download-file").click();
  const download = await downloadPromise;
  const downloadPath = await download.path();
  expect(downloadPath).not.toBeNull();
  expect(await readFile(downloadPath!, "utf8")).toBe("download-body");
  await page.locator("#upload-files").setInputFiles({
    name: "new.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("new"),
  });
  await page.locator("#upload-form button").click();
  await expect(page.locator("#notice")).toContainText("文件上传完成");

  const save = calls(mock, "PUT", "/api/workspace/file-content").at(-1)!;
  expect(save.headers["if-match"]).toBe(savedEtag);
  const upload = calls(mock, "POST", "/api/workspace/file-upload").at(-1)!;
  expect(upload.headers["content-type"]).toContain("multipart/form-data");
  expect(String(upload.body)).toContain('name="files"');
});

test("会话创建、SSE delta/snapshot 去重、未知工具事件日志和停止请求", async ({
  page,
  mock,
}) => {
  await page.goto("/");
  await connect(page);
  await page.locator("#new-chat").click();
  await expect(page.locator("#chat-identity")).toContainText("session");
  await page.locator("#history").click();
  await expect(page.locator("#messages .assistant pre")).toHaveText(
    "history restored",
  );
  await page.locator("#prompt").fill("say hello");
  await page.locator("#send").click();
  await expect(page.locator("#messages .assistant pre").last()).toHaveText(
    "Hello world",
  );
  await expect(page.locator("#log-list")).toContainText("unknown_tool_event");
  const send = calls(mock, "POST", "/api/console/chat").at(-1)!;
  expect(send.body).toMatchObject({
    session_id: expect.any(String),
    user_id: "default",
    channel: "console",
    stream: true,
    request_context: { capabilities: { tl_preview: true } },
  });
  expect((send.body as any).input[0].content[0]).toEqual({
    type: "text",
    text: "say hello",
  });
  expect(calls(mock, "POST", "/api/chats")).toHaveLength(1);

  // The mock response is finite; this checks the stop contract/request only,
  // not that a mock route can reproduce backend task cancellation.
  mock.delayChat = 300;
  await page.locator("#prompt").fill("stop me");
  await page.locator("#send").click();
  await expect(page.locator("#stop")).toBeEnabled();
  await page.locator("#stop").click();
  await expect
    .poll(() => calls(mock, "POST", "/api/console/chat/stop").length)
    .toBe(1);
  await expect(page.locator("#stream-status")).toContainText(/中断|异常/);
});

test("401 可见、日志结构完整、敏感字段脱敏且响应文本不执行 XSS", async ({
  page,
  mock,
}) => {
  mock.authEnabled = true;
  mock.force401 = true;
  await page.goto("/");
  await page.locator("#token").fill("token-secret");
  await page.locator("#connect-form button").first().click();
  await expect(page.locator("#notice")).toContainText("401");
  await page.getByText("使用账号密码登录", { exact: true }).click();
  await page.locator("#username").fill("tester");
  await page.locator("#password").fill("password-secret");
  await page.locator("#login-form button").click();
  await expect(page.locator("#notice")).toContainText("401");
  await page.locator('button[data-tab="manage"]').click();
  await page.locator("#config-provider-id").fill("demo");
  await page.locator("#provider-url").fill("http://mock");
  await page.locator("#provider-key").fill("api-key-secret");
  await page.locator("#provider-form button").click();
  await expect(page.locator("#notice")).toContainText("401");
  await page.locator("#load-models").click();
  await expect(page.locator("#management-result")).toContainText(
    "<img src=x onerror=alert(1)>",
  );
  expect(await page.locator("#management-result img").count()).toBe(0);
  expect(
    await page.evaluate(
      async () => (await fetch("/api/intentionally-unmocked")).status,
    ),
  ).toBe(404);

  const logText = await page.locator("#log-list").textContent();
  expect(logText).toContain("GET /api/auth/status");
  expect(logText).toContain("401");
  expect(logText).toContain("request");
  expect(logText).toContain("response");
  expect(logText).toMatch(/\d+ ms/);
  for (const secret of ["token-secret", "password-secret", "api-key-secret"])
    expect(logText).not.toContain(secret);

  const downloadPromise = page.waitForEvent("download");
  await page.locator("#export-logs").click();
  const download = await downloadPromise;
  const path = await download.path();
  expect(path).not.toBeNull();
  const exported = await readFile(path!, "utf8");
  for (const secret of ["token-secret", "password-secret", "api-key-secret"])
    expect(exported).not.toContain(secret);
});

test("Agent 核心能力验证：收件箱、技能管理、工作区检查点与 Markdown 渲染", async ({
  page,
  mock,
}) => {
  await page.goto("/");
  await connect(page);

  // 1. 收件箱与审批
  await page.locator('button[data-tab="inbox"]').click();
  await expect(page.locator("#inbox-events-list")).toContainText(
    "后台分析任务完成",
  );
  await expect(page.locator("#inbox-approval-list")).toContainText(
    "rm -rf /tmp/cache",
  );
  await page.locator(".btn-trace-link").first().click();
  await expect(page.locator("#inbox-trace-detail")).toContainText(
    "Trace finished successfully",
  );
  await page.locator(".btn-approve-single").first().click();
  await expect(page.locator("#notice")).toContainText("已批准执行");

  // 2. 技能管理面板
  await page.locator('button[data-tab="skills"]').click();
  await expect(page.locator("#skills-list-grid")).toContainText("pdf_reader");
  await expect(page.locator("#skills-list-grid")).toContainText("web_scraper");
  const toggleBtn = page.locator(".btn-toggle-skill").first();
  await expect(toggleBtn).toContainText("禁用技能");
  await toggleBtn.click();
  await expect(page.locator("#notice")).toContainText("已禁用");

  // 3. 文件检查点
  await page.locator('button[data-tab="files"]').click();
  await page.locator("#new-chat").click();
  await expect(page.locator("#checkpoints-list")).toContainText("初始基线快照");

  // 4. 会话对话中的 Markdown 渲染
  await page.locator('button[data-tab="chat"]').click();
  await page.locator("#prompt").fill("test markdown");
  await page.locator("#send").click();
  await expect(
    page.locator("#messages .assistant .msg-markdown-body"),
  ).toBeVisible();
});
