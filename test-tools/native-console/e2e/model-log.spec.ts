import { readFile } from "node:fs/promises";
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function connect(page: import("@playwright/test").Page): Promise<void> {
  await page.locator("#token").fill("");
  await page.locator("#connect-form button").first().click();
  await expect(page.locator("#connection-status")).toContainText("已连接");
}

const localDiagnostics = [
  {
    type: "model_log",
    object: "diagnostic",
    id: "diag-request",
    event: "request",
    level: "DEBUG",
    payload: { api_key: "model-secret", messages: [{ role: "user" }] },
    timestamp: "2026-09-18T00:00:01Z",
    run_id: "run-local",
    invocation_id: "inv-local",
  },
  {
    type: "model_log",
    object: "diagnostic",
    id: "diag-response",
    event: "model_response",
    level: "DEBUG",
    payload: { text: "model response body", total_chars: 19 },
    timestamp: "2026-09-18T00:00:02Z",
    run_id: "run-local",
    invocation_id: "inv-local",
  },
  {
    type: "model_log",
    object: "diagnostic",
    id: "diag-tool",
    event: "tool_prompt_conversion",
    level: "DEBUG",
    payload: { tool: "shell", converted: true },
    timestamp: "2026-09-18T00:00:03Z",
    run_id: "run-local",
    invocation_id: "inv-local",
  },
  {
    type: "model_log",
    object: "diagnostic",
    id: "diag-error",
    event: "error",
    level: "ERROR",
    payload: { message: "safe diagnostic" },
    timestamp: "2026-09-18T00:00:04Z",
    run_id: "run-local",
    invocation_id: "inv-local",
  },
];

function localStream(): string {
  const events = [
    localDiagnostics[0],
    localDiagnostics[0],
    ...localDiagnostics.slice(1),
    {
      object: "content",
      type: "text",
      delta: true,
      index: 0,
      msg_id: "m-model-log",
      text: "Hello diagnostics",
    },
    {
      object: "message",
      id: "m-model-log",
      role: "assistant",
      content: [{ type: "text", text: "Hello diagnostics" }],
      status: "completed",
    },
    { object: "response", status: "completed", output: [] },
  ];
  return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
}

test("本地模型诊断独立于聊天正文、去重并支持筛选清空导出", async ({
  page,
  mock: _mock,
}) => {
  const requestBodies: unknown[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/console/chat") {
      try {
        requestBodies.push(JSON.parse(request.postData() || "{}"));
      } catch {
        requestBodies.push(request.postData());
      }
    }
  });
  await page.route("**/api/console/chat", (route) =>
    route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: localStream(),
    }),
  );

  await page.goto("/");
  await connect(page);
  await page.locator("#new-chat").click();
  await expect(page.locator("#debug-toggle")).not.toBeChecked();
  await page.locator("#prompt").fill("show diagnostics");
  await page.locator("#send").click();
  await expect(page.locator("#messages .assistant pre").last()).toHaveText(
    "Hello diagnostics",
  );
  await page.locator("#toggle-model-logs-btn").click();
  await expect(page.locator("#model-log-list details")).toHaveCount(4);
  await expect(page.locator("#messages")).not.toContainText(
    "model response body",
  );
  await expect(page.locator("#model-log-list")).not.toContainText(
    "model-secret",
  );

  const responseEntry = page
    .locator("#model-log-list details")
    .filter({ hasText: "model_response" })
    .first();
  await responseEntry.locator("summary").click();
  await expect(responseEntry).toContainText("model_response");
  await page.locator("#model-log-filter").fill("error");
  await expect(page.locator("#model-log-list details")).toHaveCount(1);

  const exportPromise = page.waitForEvent("download");
  await page.locator("#export-model-logs").click();
  const exported = await (await exportPromise).path();
  expect(exported).not.toBeNull();
  expect(await readFile(exported!, "utf8")).not.toContain("model-secret");

  await page.locator("#clear-model-logs").click();
  await expect(page.locator("#model-log-list details")).toHaveCount(0);
  await expect(page.locator("#model-log-empty")).toContainText("暂无模型诊断");

  await page.locator("#model-log-filter").fill("");
  await page.locator("#debug-toggle").check();
  await page.locator("#prompt").fill("debug request");
  await page.locator("#send").click();
  await expect.poll(() => requestBodies.length).toBe(2);
  await expect(page.locator("#messages .assistant pre").last()).toHaveText(
    "Hello diagnostics",
  );
  expect(requestBodies[0]).toMatchObject({
    request_context: { capabilities: { tl_preview: true } },
  });
  expect(
    (requestBodies[0] as any).request_context.capabilities.model_debug,
  ).toBe(undefined);
  expect(requestBodies[1]).toMatchObject({
    request_context: {
      capabilities: { tl_preview: true, model_debug: true },
    },
  });
});

test("服务模式传递 debug 并捕获 model_log 而不污染正文", async ({
  page,
  mock: _mock,
}) => {
  const requestBodies: unknown[] = [];
  let serviceExternalSession = "";
  await page.route("**/api/service/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith("/health"))
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ mode: "server", status: "ok" }),
      });
    if (path.endsWith("/assistant"))
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ attachments: false, model: "service-demo" }),
      });
    if (path.endsWith("/sessions"))
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          serviceExternalSession
            ? [
                {
                  id: "session-service",
                  external_id: serviceExternalSession,
                  channel_id: "native-console",
                },
              ]
            : [],
        ),
      });
    if (path.endsWith("/runs") && request.method() === "POST") {
      const body = JSON.parse(request.postData() || "{}");
      serviceExternalSession = String(body.sessionid || "");
      requestBodies.push(body);
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          id: "run-service",
          session_id: "session-service",
        }),
      });
    }
    if (path.endsWith("/runs/run-service/events")) {
      const events = [
        {
          ...localDiagnostics[1],
          id: "service-response",
          run_id: "run-service",
        },
        { type: "text", text: "Hello service diagnostics", delta: true },
        { type: "end", status: "completed" },
      ];
      const body = `id: 1\ndata: ${JSON.stringify(events[0])}\n\nid: 2\ndata: ${JSON.stringify(events[1])}\n\nevent: end\ndata: ${JSON.stringify(events[2])}\n\n`;
      return route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body,
      });
    }
    return route.fulfill({ status: 404, body: "not found" });
  });

  await page.goto("/");
  await page.locator("#chat-mode").selectOption("service");
  await page.locator("#connect-form button").first().click();
  await expect(page.locator("#connection-status")).toContainText("服务已连接");
  await page.locator("#new-chat").click();
  await page.locator("#debug-toggle").check();
  await page.locator("#prompt").fill("service diagnostics");
  await page.locator("#send").click();
  await expect(page.locator("#messages .assistant")).toContainText(
    "Hello service diagnostics",
  );
  expect(requestBodies[0]).toMatchObject({ debug: true });
  await page.locator("#toggle-model-logs-btn").click();
  await expect(page.locator("#model-log-list details")).toHaveCount(1);
  await expect(page.locator("#messages")).not.toContainText(
    "model response body",
  );
});
