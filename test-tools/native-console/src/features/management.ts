// Management tab: model selection, provider config, channel config, cron jobs,
// plus the TL Provider panel (delegated to TLPanel).

import type { ApiClient } from "../api";
import type { RecordValue } from "../chat";
import { action, run } from "../core/app";
import { $, input, json, node, selectOptions, value } from "../core/dom";
import { state } from "../core/state";
import type { TLPanel } from "../tl";

export function managementResult(api: ApiClient, data: unknown): void {
  $("management-result").textContent = json(api.safe(data));
}

export function bindManagement(api: ApiClient, tl: TLPanel): void {
  const result = (data: unknown): void => managementResult(api, data);

  action("load-models", async () => {
    await tl.load();
    try {
      const active = await api.request<RecordValue>("/api/models/active");
      if (active.model) {
        $("chat-provider").textContent =
          `${active.provider_id || "model"}: ${active.model}`;
      }
    } catch {
      // Ignore
    }
  });

  action("model-form", async () =>
    result(
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
    result(
      await api.request(
        "/api/models/" +
          encodeURIComponent(input("config-provider-id")) +
          "/config",
        {
          method: "PUT",
          body: {
            base_url: input("provider-url"),
            ...(value("provider-key")
              ? { api_key: value("provider-key") }
              : {}),
          },
        },
      ),
    );
    $<HTMLInputElement>("provider-key").value = "";
  });

  action("load-channels", async () => {
    const channels = await api.request<RecordValue>("/api/config/channels");
    state.knownChannels = Object.keys(channels);
    result(channels);
    selectOptions(
      "channel-name",
      state.knownChannels.map((name) => ({ value: name, label: name })),
    );
  });

  action("read-channel", async () => {
    const name = input("channel-name");
    const config = await api.request(
      "/api/config/channels/" + encodeURIComponent(name),
    );
    $<HTMLTextAreaElement>("channel-json").value = json(config);
    state.loadedChannel = name;
  });

  action("channel-form", async () => {
    if (state.loadedChannel !== input("channel-name"))
      throw new Error("请先读取当前渠道配置");
    const config = JSON.parse(value("channel-json"));
    if (!config || Array.isArray(config) || typeof config !== "object")
      throw new Error("配置必须是 JSON 对象");
    result(
      await api.request(
        "/api/config/channels/" + encodeURIComponent(state.loadedChannel),
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
    result(jobs);
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
              session_id: state.currentChat?.session_id || "native-validation",
            },
            mode: "final",
          },
        },
      });
      await loadCron();
    },
    "定时任务已创建，保持暂停状态",
  );
}
