import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("本地 TL 在响应结束前显示预览，确认后替换为正式正文", async ({ page, mock }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (String(input).endsWith("/api/console/chat")) {
        (window as any).chatRequest = JSON.parse(String(init?.body));
        const encoder = new TextEncoder();
        const identity = { run_id: "r", invocation_id: "i", attempt_id: "a" };
        const body = new ReadableStream({
          start(controller) {
            const emit = (event: unknown) =>
              controller.enqueue(
                encoder.encode(`data: ${JSON.stringify(event)}\n\n`),
              );
            emit({ type: "preview_start", ...identity });
            emit({
              type: "preview_update",
              ...identity,
              kind: "final",
              item_index: 0,
              text: "正在逐步生成",
            });
            (window as any).finishPreview = () => {
              emit({ type: "preview_clear", ...identity, reason: "commit" });
              emit({
                object: "response",
                status: "completed",
                output: [
                  {
                    id: "m",
                    role: "assistant",
                    content: [{ type: "text", text: "正式完整答案" }],
                  },
                ],
              });
              controller.close();
            };
          },
        });
        return Promise.resolve(
          new Response(body, {
            headers: { "Content-Type": "text/event-stream" },
          }),
        );
      }
      return original(input, init);
    };
  });
  await page.goto("/");
  await page.locator("#connect-form button").first().click();
  await expect(page.locator("#connection-status")).toContainText("已连接");
  await page.locator("#prompt").fill("请给我一个详细答案");
  await page.locator("#send").click();
  await expect(page.locator(".local-preview-timeline")).toContainText(
    "正在逐步生成",
  );
  await expect(page.locator("#stop")).toBeEnabled();
  expect(
    await page.evaluate(
      () => (window as any).chatRequest.request_context.capabilities,
    ),
  ).toEqual({ tl_preview: true });
  await page.evaluate(() => (window as any).finishPreview());
  await expect(page.locator(".local-preview-timeline")).toHaveCount(0);
  await expect(page.locator("#messages .msg-markdown-body")).toContainText(
    "正式完整答案",
  );
  await expect(page.locator("#stream-status")).toContainText("完成");
});
