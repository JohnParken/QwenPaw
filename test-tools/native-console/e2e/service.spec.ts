import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("服务聊天使用 /v1 契约并按事件顺序呈现工具与预览", async ({ page }) => {
  const calls: { method: string; path: string; body: any }[] = [];
  let externalSession = "";
  await page.route("**/api/service/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    let body: any = undefined;
    try {
      body = request.postDataJSON();
    } catch {
      // Multipart/file requests are not used by this flow.
    }
    calls.push({ method: request.method(), path, body });
    if (path === "/api/service/health")
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok" }),
      });
    if (path === "/api/service/assistant")
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          name: "Demo assistant",
          model: "demo-model",
          model_protocol: "tl",
          version: "platform-tl-v1",
          attachments: false,
        }),
      });
    if (path === "/api/service/sessions") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          externalSession
            ? [
                {
                  id: "internal-session",
                  external_id: externalSession,
                  state: {},
                },
              ]
            : [],
        ),
      });
    }
    if (path === "/api/service/runs" && request.method() === "POST") {
      externalSession = body.sessionid;
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          id: "run-service-1",
          session_id: "internal-session",
          status: "queued",
        }),
      });
    }
    if (path === "/api/service/runs/run-service-1/events") {
      const stream = [
        'id: 1\ndata: {"type":"text","text":"before ","delta":true}\n\n',
        'id: 2\ndata: {"type":"preview_start","run_id":"run-service-1","invocation_id":"i","attempt_id":"a"}\n\n',
        'id: 3\ndata: {"type":"preview_update","run_id":"run-service-1","invocation_id":"i","attempt_id":"a","kind":"tool_call","item_index":0,"text":"{\\"command\\":\\"echo hi\\"}"}\n\n',
        'id: 4\ndata: {"type":"preview_clear","run_id":"run-service-1","invocation_id":"i","attempt_id":"a","reason":"commit"}\n\n',
        'id: 5\ndata: {"type":"tool","tool_call_id":"call-1","name":"shell","arguments":{"command":"echo hi"},"status":"running"}\n\n',
        'id: 6\ndata: {"type":"tool_output","tool_call_id":"call-1","text":"hi","status":"completed"}\n\n',
        'id: 7\ndata: {"type":"text","text":"after","delta":true}\n\n',
        'event: end\ndata: {"status":"completed"}\n\n',
      ].join("");
      return route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: stream,
      });
    }
    if (path === "/api/service/sessions/internal-session/messages") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            seq: 1,
            run_id: "run-service-1",
            payload: { role: "user", content: "hello service" },
          },
          {
            seq: 2,
            run_id: "run-service-1",
            payload: {
              role: "assistant",
              type: "run_timeline",
              run_id: "run-service-1",
              status: "completed",
              events: [
                { type: "text", text: "history", delta: true },
                {
                  type: "tool",
                  tool_call_id: "call-1",
                  name: "shell",
                  arguments: { command: "echo history" },
                  status: "completed",
                  log: "history output",
                },
              ],
            },
          },
        ]),
      });
    }
    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: `unknown service route ${path}` }),
    });
  });

  await page.goto("/");
  await page.locator("#chat-mode").selectOption("service");
  await page.locator("#connect-form button").first().click();
  await expect(page.locator("#connection-status")).toContainText("服务已连接");
  await page.locator('[data-tab="manage"]').click();
  await expect(page.locator("#service-tl-model")).toContainText(
    "platform-tl-v1",
  );
  await expect(page.locator("#tl-panel")).toBeHidden();
  await page.locator('[data-tab="chat"]').click();
  await page.locator("#new-chat").click();
  await page.locator("#prompt").fill("hello service");
  await page.locator("#send").click();
  await expect(page.locator(".ordered-timeline")).toContainText("before");
  await expect(page.locator(".ordered-timeline")).toContainText("after");
  await expect(page.locator(".tool-card")).toContainText("成功");
  await expect(page.locator(".preview-card")).toHaveCount(0);
  const submit = calls.find((call) => call.path === "/api/service/runs");
  expect(submit?.body).toMatchObject({
    usrid: "default",
    channelid: "native-console",
    message: "hello service",
  });
  expect(calls.find((call) => call.path.endsWith("/events"))?.method).toBe(
    "GET",
  );
  await page.locator("#history").click();
  await expect(page.locator("#messages")).toContainText("history");
  await expect(page.locator("#messages")).toContainText("history output");
});

for (const decision of ["approve", "deny", "cancel"] as const) {
  test(`服务审批 ${decision} 保持可点击并等待持久终态`, async ({ page }) => {
    let decided = false;
    let submitted = false;
    let decisions = 0;
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/api/service/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      const reply = (body: unknown) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(body),
        });
      if (path.endsWith("/health")) return reply({ status: "ok" });
      if (path.endsWith("/assistant"))
        return reply({ model: "mock", attachments: false });
      if (path.endsWith("/sessions"))
        return reply(
          submitted ? [{ id: "session", external_id: "external" }] : [],
        );
      if (path === "/api/service/runs") {
        submitted = true;
        return reply({ id: "run", session_id: "session" });
      }
      if (path.endsWith("/decision")) {
        expect(route.request().postDataJSON()).toEqual({
          approved: decision === "approve",
        });
        decisions++;
        decided = true;
        return reply({
          status: decision === "approve" ? "approved" : "denied",
        });
      }
      if (path.endsWith("/cancel")) {
        decisions++;
        decided = true;
        return reply({ status: "running" });
      }
      if (path.endsWith("/events")) {
        const status = decision === "cancel" ? "cancelled" : "completed";
        const events = decided
          ? [
              {
                type: "approval",
                approval: {
                  id: "approval",
                  call_id: "call",
                  status: decision === "approve" ? "approved" : "denied",
                },
              },
              {
                type: "tool",
                tool_call_id: "call",
                status:
                  decision === "approve"
                    ? "completed"
                    : decision === "deny"
                      ? "denied"
                      : "cancelled",
                output: { result: "done" },
              },
            ]
          : [
              {
                type: "tool",
                tool_call_id: "call",
                name: "shell",
                arguments: { command: "echo test" },
                status: "awaiting_approval",
              },
              {
                type: "approval",
                tool_call_id: "call",
                approval: {
                  id: "approval",
                  call_id: "call",
                  name: "shell",
                  status: "pending",
                },
              },
            ];
        const body =
          events
            .map(
              (event, i) =>
                `id: ${(decided ? 3 : 1) + i}\ndata: ${JSON.stringify(event)}\n\n`,
            )
            .join("") +
          (decided
            ? `event: end\ndata: ${JSON.stringify({ status })}\n\n`
            : "");
        return route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body,
        });
      }
      return reply({
        id: "run",
        status: decision === "cancel" ? "cancelled" : "completed",
      });
    });
    await page.goto("/");
    await page.locator("#chat-mode").selectOption("service");
    await page.locator("#connect-form button").first().click();
    await expect(page.locator("#connection-status")).toContainText(
      "服务已连接",
    );
    await page.locator("#prompt").fill("tool please");
    await page.locator("#send").click();
    await expect(page.locator(".approval-card")).toBeVisible();
    if (decision === "cancel") await page.locator("#stop").click();
    else
      await page
        .locator(decision === "approve" ? ".btn-approve" : ".btn-deny")
        .click();
    await expect(page.locator(".approval-card")).toHaveCount(0);
    await expect(page.locator(".tool-status-badge")).toContainText(
      decision === "approve" ? "成功" : decision === "deny" ? "拒绝" : "取消",
    );
    await expect(page.locator("#chat-mode")).toBeEnabled();
    expect(decisions).toBe(1);
    expect(errors).toEqual([]);
  });
}

test("服务 TL 上传附件并通过文件 ID 提交任务", async ({ page, mock }) => {
  let submitted: any;
  let uploaded = false;
  await page.route("**/api/service/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const reply = (body: unknown) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    if (path.endsWith("/health"))
      return reply({ mode: "server", status: "ok" });
    if (path.endsWith("/assistant"))
      return reply({ model_protocol: "tl", model: "tl", attachments: true });
    if (path.endsWith("/sessions")) return reply([]);
    if (path.endsWith("/files")) {
      uploaded = true;
      expect(route.request().headers()["content-type"]).toContain(
        "multipart/form-data",
      );
      return reply({ id: "owned-file" });
    }
    if (path.endsWith("/runs")) {
      submitted = route.request().postDataJSON();
      return reply({ id: "run", session_id: "session" });
    }
    if (path.endsWith("/events"))
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'event: end\ndata: {"status":"completed"}\n\n',
      });
    return reply({});
  });
  await page.goto("/");
  await page.locator("#chat-mode").selectOption("service");
  await page.locator("#connect-form button").first().click();
  await expect(page.locator("#connection-status")).toContainText("服务已连接");
  await page
    .locator("#chat-file")
    .setInputFiles({
      name: "notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("private document"),
    });
  await page.locator("#prompt").fill("summarize attachment");
  await page.locator("#send").click();
  await expect.poll(() => submitted?.attachments).toEqual(["owned-file"]);
  expect(uploaded).toBe(true);
  expect(submitted.message).toBe("summarize attachment");
});
