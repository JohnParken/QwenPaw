// Session lifecycle for both modes: list, create, select, and the sidebar.
//
// Local mode lists `/api/chats?channel=console`; service mode lists durable
// `/v1` sessions. The hidden `#chats` select stays the single selection source
// (the browser tests drive it directly), and the sidebar mirrors it.

import type { ApiClient } from "../api";
import type { RecordValue } from "../chat";
import type { ServiceClient, ServiceSession } from "../service";
import { action, chatMode } from "../core/app";
import { $, input, node, selectOptions } from "../core/dom";
import { state } from "../core/state";

/** Stable identity helpers: the durable id and the external session key. */
export const serviceSessionId = (session: ServiceSession): string =>
  String(session.id || session.external_id || "");

export const serviceSubmitSessionId = (session: ServiceSession): string =>
  String(session.external_id || session.id || "");

export interface SessionHandlers {
  refreshChats(selected?: string): Promise<void>;
  refreshServiceSessions(
    selected?: string,
    preserveCurrent?: boolean,
  ): Promise<void>;
  createChat(): Promise<void>;
  updateChatSelection(): void;
  renderSidebarSessions(): void;
}

export function bindSessions(deps: {
  api: ApiClient;
  service: ServiceClient;
  /** Injected to avoid a module cycle: sessions renders, chat clears logs. */
  clearModelLogs: () => void;
  loadCheckpoints: () => Promise<void>;
}): SessionHandlers {
  const { api, service, clearModelLogs, loadCheckpoints } = deps;

  function renderSidebarSessions(): void {
    const container = $("sidebar-sessions-list");
    container.replaceChildren();
    const sessions =
      chatMode() === "service"
        ? state.serviceSessions.map((session) => ({
            id: serviceSessionId(session),
            title: String(
              session.external_id || session.id || "服务会话",
            ).slice(0, 40),
          }))
        : state.chats.map((chat) => ({
            id: String(chat.id),
            title: String(chat.name || `会话 ${String(chat.id).slice(0, 8)}`),
          }));
    for (const session of sessions) {
      const item = node("button");
      item.type = "button";
      item.className =
        "sidebar-session-item" +
        ((
          chatMode() === "service"
            ? state.currentServiceSession?.id === session.id
            : state.currentChat?.id === session.id
        )
          ? " active"
          : "");
      item.dataset.id = session.id;
      const title = node("span", session.title);
      title.className = "session-name-truncate";
      item.append(title);
      item.addEventListener("click", () => {
        if (state.busy) return;
        $<HTMLSelectElement>("chats").value = session.id;
        updateChatSelection();
      });
      container.append(item);
    }
  }

  function updateChatSelection(): void {
    clearModelLogs();
    if (chatMode() === "service") {
      state.currentServiceSession = state.serviceSessions.find(
        (session) => serviceSessionId(session) === input("chats"),
      );
      state.currentChat = undefined;
      $("chat-identity").textContent = state.currentServiceSession
        ? `服务会话 ${String(state.currentServiceSession.external_id || state.currentServiceSession.id).slice(0, 16)}`
        : "未选择服务会话";
      $("messages").replaceChildren();
      state.loadedFile = null;
      renderSidebarSessions();
      return;
    }
    state.currentChat = state.chats.find((chat) => chat.id === input("chats"));
    state.currentServiceSession = undefined;
    $("chat-identity").textContent = state.currentChat
      ? `${state.currentChat.name || "会话 " + state.currentChat.id.slice(0, 8)} · session ${state.currentChat.session_id}`
      : "未选择会话";
    $("messages").replaceChildren();
    state.loadedFile = null;
    renderSidebarSessions();
    void loadCheckpoints();
  }

  async function refreshServiceSessions(
    selected = state.currentServiceSession?.id,
    preserveCurrent = false,
  ): Promise<void> {
    const previousSessionId = state.currentServiceSession
      ? serviceSessionId(state.currentServiceSession)
      : "";
    state.serviceSessions = (await service.sessions()).filter(
      (session) =>
        !session.channel_id || String(session.channel_id) === service.channel,
    );
    const selectedSession = state.serviceSessions.find(
      (session) =>
        serviceSessionId(session) === selected ||
        session.external_id === selected,
    );
    selectOptions(
      "chats",
      [
        { value: "", label: "选择服务会话" },
        ...state.serviceSessions.map((session) => ({
          value: serviceSessionId(session),
          label: String(session.external_id || session.id),
        })),
      ],
      selectedSession ? serviceSessionId(selectedSession) : selected,
    );
    if (
      preserveCurrent &&
      selectedSession &&
      previousSessionId === serviceSessionId(selectedSession)
    ) {
      state.currentServiceSession = selectedSession;
      $("chat-identity").textContent =
        `服务会话 ${String(selectedSession.external_id || selectedSession.id).slice(0, 16)}`;
      renderSidebarSessions();
      return;
    }
    updateChatSelection();
  }

  async function refreshChats(selected = state.currentChat?.id): Promise<void> {
    if (chatMode() === "service") {
      await refreshServiceSessions(selected);
      return;
    }
    state.chats = await api.request<RecordValue[]>(
      "/api/chats?channel=console",
    );
    selectOptions(
      "chats",
      [
        { value: "", label: "选择会话" },
        ...state.chats.map((chat) => ({
          value: chat.id,
          label: chat.name || chat.id,
        })),
      ],
      selected,
    );
    updateChatSelection();
  }

  async function createChat(): Promise<void> {
    if (chatMode() === "service") {
      const session: ServiceSession = {
        id: crypto.randomUUID(),
        external_id: crypto.randomUUID(),
        channel_id: service.channel,
        state: "idle",
      };
      state.serviceSessions.unshift(session);
      selectOptions(
        "chats",
        state.serviceSessions.map((item) => ({
          value: serviceSessionId(item),
          label: String(item.external_id || item.id),
        })),
        serviceSessionId(session),
      );
      updateChatSelection();
      return;
    }
    const chat = await api.request<RecordValue>("/api/chats", {
      method: "POST",
      body: {
        name: "验证会话 " + new Date().toLocaleTimeString(),
        session_id: crypto.randomUUID(),
        user_id: "default",
        channel: "console",
      },
    });
    state.chats.unshift(chat);
    selectOptions(
      "chats",
      state.chats.map((item) => ({
        value: item.id,
        label: item.name || item.id,
      })),
      chat.id,
    );
    updateChatSelection();
  }

  action("refresh-chats", () => refreshChats());
  $("chats").addEventListener("change", () => updateChatSelection());
  action("new-chat", createChat, "会话已创建");

  return {
    refreshChats,
    refreshServiceSessions,
    createChat,
    updateChatSelection,
    renderSidebarSessions,
  };
}
