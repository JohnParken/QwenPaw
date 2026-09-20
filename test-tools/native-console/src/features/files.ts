// Workspace file browser and ETag-guarded text editor.
//
// Every request carries the selected chat's `X-Chat-Id` so the backend resolves
// the same project root the chat turn uses.

import type { ApiClient } from "../api";
import type { RecordValue } from "../chat";
import { action, run } from "../core/app";
import { $, download, input, node, query, value } from "../core/dom";
import { state } from "../core/state";

/** Matches the backend's editable-text ceiling. */
const MAX_TEXT_BYTES = 2 * 1024 * 1024;
const CHUNK_BYTES = 256 * 1024;

export function bindFiles(api: ApiClient): void {
  const fileHeaders = (): Record<string, string> | undefined =>
    state.currentChat
      ? { "X-Chat-Id": String(state.currentChat.id) }
      : undefined;
  const fileRoot = (): string => input("file-root");

  async function browse(append = false): Promise<void> {
    const directory = input("directory");
    if (append && directory !== state.listedDirectory)
      throw new Error("目录已改变，请重新浏览");
    const page = await api.request<RecordValue>(
      query("/workspace/tree", {
        path: directory,
        root: fileRoot(),
        limit: 100,
        ...(append ? { cursor: state.nextCursor } : {}),
      }),
      { headers: fileHeaders() },
    );
    if (!append) $("file-list").replaceChildren();
    for (const entry of page.entries) {
      const isDir = entry.kind === "directory";
      const row = node(
        "button",
        `${isDir ? "📁" : "📄"} ${entry.name}${entry.size == null ? "" : ` (${entry.size} B)`}`,
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
    state.listedDirectory = directory;
    state.nextCursor = page.next_cursor || "";
    $<HTMLButtonElement>("more-files").disabled =
      !page.has_more || !state.nextCursor;
  }

  async function openFile(): Promise<void> {
    const path = input("file-path");
    if (!path) throw new Error("请输入文件路径");
    state.loadedFile = null;
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
          limit: CHUNK_BYTES,
        }),
        { headers: fileHeaders() },
      );
      if (etag && etag !== chunk.etag)
        throw new Error("读取期间文件已变化，请重新读取");
      etag = chunk.etag;
      content += chunk.content;
      size += new TextEncoder().encode(chunk.content).byteLength;
      if (size > MAX_TEXT_BYTES)
        throw new Error("文本超出 2 MiB 编辑上限，请下载查看");
      if (chunk.eof) break;
      if (!(chunk.next_offset > offset)) throw new Error("文件分页未前进");
      offset = chunk.next_offset;
    }
    if (!etag) throw new Error("后端未返回 ETag，无法安全编辑");
    state.loadedFile = {
      path,
      root: fileRoot(),
      chatId: state.currentChat?.id || "",
      etag,
    };
    $<HTMLTextAreaElement>("file-content").value = content;
    $("file-version").textContent = `${path} · ETag ${etag}`;
  }

  action("browse-form", () => browse());
  action("more-files", () => browse(true));
  action("open-form", openFile);

  $("file-root").addEventListener("change", () => {
    state.loadedFile = null;
    state.nextCursor = "";
    $("file-list").replaceChildren();
    $<HTMLButtonElement>("more-files").disabled = true;
  });

  action(
    "save-file",
    async () => {
      if (
        !state.loadedFile ||
        state.loadedFile.path !== input("file-path") ||
        state.loadedFile.root !== fileRoot() ||
        state.loadedFile.chatId !== (state.currentChat?.id || "")
      )
        throw new Error("请先读取当前路径的文件");
      if (
        new TextEncoder().encode(value("file-content")).byteLength >
        MAX_TEXT_BYTES
      )
        throw new Error("文本超出 2 MiB 编辑上限");
      const result = await api.request<RecordValue>(
        query("/workspace/file-content", {
          path: state.loadedFile.path,
          root: state.loadedFile.root,
        }),
        {
          method: "PUT",
          headers: { ...fileHeaders(), "If-Match": state.loadedFile.etag },
          body: { content: value("file-content") },
        },
      );
      state.loadedFile.etag = result.etag;
      $("file-version").textContent =
        `${state.loadedFile.path} · ETag ${result.etag}`;
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

  action("download-file", async () => {
    const path = input("file-path");
    if (!path) throw new Error("请输入文件路径");
    const blob = await api.request<Blob>(
      query("/workspace/file-download", { path, root: fileRoot() }),
      { headers: fileHeaders(), blob: true },
    );
    download(blob, path.split("/").pop() || "download");
  });
}
