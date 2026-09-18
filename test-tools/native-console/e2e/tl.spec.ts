import { expect } from "@playwright/test";
import { test, type MockState } from "./fixtures";

async function connect(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/");
  await page.locator("#connect-form button").first().click();
  await expect(page.locator("#connection-status")).toContainText("已连接");
}

function calls(mock: MockState, method: string, path: string) {
  return mock.calls.filter(
    (call) => call.method === method && call.path === path,
  );
}

test("读取 tlproxy/deepseek 标签、保存完整 TL 配置并区分连接测试结果", async ({
  page,
  mock,
}) => {
  await connect(page);
  await page.locator('button[data-tab="manage"]').click();

  await expect(page.locator("#tl-provider")).toHaveValue("tlproxy");
  await expect(page.locator("#tl-model")).toHaveValue("deepseek-v4-flash");
  await expect(page.locator("#tl-url")).toHaveValue("http://127.0.0.1:8089");
  for (const key of ["app_id", "tr_code", "tr_version"])
    await expect(page.locator(`#tl-${key}`)).toHaveValue("");
  await expect(page.locator("#tl-system_prompt_variable_name")).toHaveValue(
    "system_prompt",
  );

  await page.locator("#tl-key").fill("optional-tl-secret");
  await page.locator("#tl-app_id").fill("native-app");
  await page.locator("#tl-tr_code").fill("native-code");
  await page.locator("#tl-tr_version").fill("1");
  await page.locator("#save-tl").click();
  await expect(page.locator("#notice")).toContainText("TL 配置已保存");

  const save = calls(mock, "PUT", "/api/models/tlproxy/config").at(-1)!;
  expect(save.body).toMatchObject({
    base_url: "http://127.0.0.1:8089",
    api_key: "optional-tl-secret",
    tl_config: {
      app_id: "native-app",
      tr_code: "native-code",
      tr_version: "1",
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
  });
  expect(Object.hasOwn(save.body as object, "generate_kwargs")).toBe(false);

  await page.locator("#test-tl").click();
  await expect(page.locator("#tl-result")).toContainText("不代表聊天已验证");
  const providerTest = calls(mock, "POST", "/api/models/tlproxy/test").at(-1)!;
  expect(providerTest.body).toHaveProperty("tl_config");
  expect(Object.hasOwn(providerTest.body as object, "generate_kwargs")).toBe(
    false,
  );
  await expect(page.locator("#management-result")).toContainText(
    '"verification": "provider_only"',
  );

  await page.locator("#test-tl-model").click();
  await expect(page.locator("#tl-result")).toContainText("聊天测试成功");
  await expect(page.locator("#management-result")).toContainText(
    '"verification": "live"',
  );
  expect(
    calls(mock, "POST", "/api/models/tlproxy/models/test").at(-1)?.body,
  ).toEqual({ model_id: "deepseek-v4-flash" });

  mock.tlModelFailNext = true;
  await page.locator("#test-tl-model").click();
  await expect(page.locator("#tl-result")).toContainText("聊天测试失败");
  expect(calls(mock, "POST", "/api/models/tlproxy/models/test")).toHaveLength(
    2,
  );
  expect((await page.locator("#log-list").textContent()) || "").not.toContain(
    "optional-tl-secret",
  );
});

test("选择 Agent 的 TL 模型后，附件在上传前被拒绝且文本走 console chat", async ({
  page,
  mock,
}) => {
  await connect(page);
  await page.locator('button[data-tab="manage"]').click();
  await page.locator("#agent").selectOption("agent-beta");
  await expect(page.locator("#tl-active")).toContainText("agent-beta");
  await page.locator("#activate-tl").click();
  await expect(page.locator("#tl-active")).toContainText("tlproxy");
  expect(calls(mock, "PUT", "/api/models/active").at(-1)?.body).toMatchObject({
    provider_id: "tlproxy",
    model: "deepseek-v4-flash",
    scope: "agent",
    agent_id: "agent-beta",
  });

  await page.locator('button[data-tab="chat"]').click();
  await page.locator("#prompt").fill("with attachment");
  await page.locator("#chat-file").setInputFiles({
    name: "blocked.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("blocked"),
  });
  await page.locator("#send").click();
  await expect(page.locator("#notice")).toContainText("TL v1 仅支持文本输入");
  expect(calls(mock, "POST", "/api/console/upload")).toHaveLength(0);
  expect(calls(mock, "POST", "/api/chats")).toHaveLength(0);
  expect(calls(mock, "POST", "/api/console/chat")).toHaveLength(0);

  await page.locator("#chat-file").setInputFiles([]);
  await page.locator("#prompt").fill("plain TL message");
  await page.locator("#send").click();
  await expect(page.locator("#messages .assistant pre").last()).toHaveText(
    "Hello world",
  );
  const send = calls(mock, "POST", "/api/console/chat").at(-1)!;
  expect(send.body).toMatchObject({
    user_id: "default",
    channel: "console",
    stream: true,
  });
  expect((send.body as any).input[0].content).toEqual([
    { type: "text", text: "plain TL message" },
  ]);
  expect(calls(mock, "POST", "/api/console/upload")).toHaveLength(0);
});

test("切回普通 Provider 后，聊天恢复通过 console chat 请求", async ({
  page,
  mock,
}) => {
  await connect(page);
  await page.locator('button[data-tab="manage"]').click();
  await page.locator("#activate-tl").click();
  await expect(page.locator("#tl-active")).toContainText("tlproxy");

  await page.locator("#provider-id").fill("demo");
  await page.locator("#model-id").fill("demo-model");
  await page.locator("#model-form button").click();
  await expect(page.locator("#management-result")).toContainText("demo-model");
  expect(calls(mock, "PUT", "/api/models/active").at(-1)?.body).toMatchObject({
    provider_id: "demo",
    model: "demo-model",
    scope: "agent",
  });

  await page.locator('button[data-tab="chat"]').click();
  await page.locator("#prompt").fill("ordinary provider message");
  await page.locator("#send").click();
  await expect(page.locator("#messages .assistant pre").last()).toHaveText(
    "Hello world",
  );
  expect(calls(mock, "POST", "/api/console/chat").at(-1)?.body).toMatchObject({
    channel: "console",
    stream: true,
  });
  await expect(page.locator("#chat-provider")).toContainText(
    "demo / demo-model",
  );
});
