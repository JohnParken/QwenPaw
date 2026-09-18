import {SseParser} from "./sse.js";
const $ = (id) => document.getElementById(id);
const filesByUser = new Map();
let active = null, epoch = 0;
const user = () => $("user").value;
const attachments = () => $("attachments").value.split(",").map(x => x.trim()).filter(Boolean);
function log(value) { $("output").textContent = ($("output").textContent + "\n" + (typeof value === "string" ? value : JSON.stringify(value, null, 2))).slice(-131072); }
async function api(path, options = {}, identity = user()) {
  const headers = new Headers(options.headers);
  headers.set("X-QwenPaw-User", identity);
  const response = await fetch(path, {...options, headers});
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  return response;
}
const post = (body) => ({method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
function reset() { epoch++; active?.controller?.abort(); active = null; $("approvals").replaceChildren(); $("internal-session").value = ""; }
function isCurrent(run) { return active === run && run.epoch === epoch; }
function approval(run, item) {
  if (run.approvals.has(item.id)) return;
  run.approvals.add(item.id);
  const row = document.createElement("div");
  row.append(`Approval ${item.id} (tool call ${item.call_id || "unknown"}): `);
  for (const approved of [true, false]) {
    const button = document.createElement("button");
    button.textContent = approved ? "Approve" : "Deny";
    button.onclick = async () => {
      if (!isCurrent(run)) return;
      const buttons = row.querySelectorAll("button"); buttons.forEach(b => b.disabled = true);
      try {
        const result = await (await api(`/v1/approvals/${encodeURIComponent(item.id)}/decision`, post({approved}), run.user)).json();
        if (isCurrent(run)) { row.textContent = `Approval ${item.id}: ${result.status}`; log(result); }
      } catch (error) { if (isCurrent(run)) { log(String(error)); buttons.forEach(b => b.disabled = false); } }
    };
    row.append(button);
  }
  $("approvals").append(row);
}
async function stream(run) {
  run.controller?.abort();
  const controller = new AbortController(); run.controller = controller;
  for (let retry = 0; isCurrent(run) && !controller.signal.aborted && !run.done && retry < 5; retry++) {
    try {
      const response = await api(`/v1/runs/${encodeURIComponent(run.id)}/events`, {headers: {Accept: "text/event-stream", "Last-Event-ID": String(run.cursor)}, signal: controller.signal}, run.user);
      const reader = response.body.getReader(), decoder = new TextDecoder(), parser = new SseParser();
      try {
        while (isCurrent(run) && !controller.signal.aborted && !run.done) {
          const {done, value} = await reader.read();
          if (done) break;
          for (const event of parser.push(decoder.decode(value, {stream: true}))) {
            if (!isCurrent(run) || controller.signal.aborted) break;
            const id = event.id === undefined ? null : Number(event.id);
            if (id !== null && (!Number.isSafeInteger(id) || id < 0)) throw new Error("Invalid event cursor");
            if (id !== null && id <= run.cursor) continue;
            const payload = JSON.parse(event.data);
            log(payload);
            if (payload.approval?.id) approval(run, payload.approval);
            if (id !== null) run.cursor = id;
            if (payload.type === "terminal" || event.event === "end") run.done = true;
          }
        }
      } finally { await reader.cancel(); reader.releaseLock(); }
    } catch (error) {
      if (!isCurrent(run) || controller.signal.aborted) return;
      log(`Stream interrupted: ${error.message}`);
    }
    if (!run.done && isCurrent(run) && !controller.signal.aborted) {
      log(`Reconnecting from event ${run.cursor}`);
      await new Promise(resolve => setTimeout(resolve, Math.min(500 * (retry + 1), 2000)));
    }
  }
  if (isCurrent(run) && !run.done && !controller.signal.aborted) log("Reconnect limit reached. Use Reconnect to retry.");
}
$("user").oninput = () => { reset(); $("output").textContent = ""; $("history").textContent = ""; $("files").textContent = ""; $("attachments").value = [...(filesByUser.get(user()) || [])].join(", "); };
$("send").onclick = async () => {
  reset(); const version = epoch, identity = user(); $("output").textContent = "";
  try {
    const created = await (await api("/v1/runs", post({usrid: identity, channelid: $("channel").value, sessionid: $("session").value, request_id: crypto.randomUUID(), message: $("message").value, attachments: attachments()}), identity)).json();
    if (version !== epoch) return;
    active = {id: created.id || created.run_id, user: identity, epoch: version, cursor: 0, done: false, approvals: new Set()};
    $("internal-session").value = created.session_id;
    log(created); await stream(active);
  } catch (error) { if (version === epoch) log(String(error)); }
};
$("reconnect").onclick = () => { if (active) void stream(active); };
$("cancel").onclick = async () => {
  const run = active; if (!run) return;
  try { const result = await (await api(`/v1/runs/${encodeURIComponent(run.id)}/cancel`, post({}), run.user)).json(); if (isCurrent(run)) log(result); }
  catch (error) { if (isCurrent(run)) log(String(error)); }
};
$("refresh").onclick = async () => {
  const version = epoch, identity = user();
  try {
    const items = await (await api("/v1/sessions", {}, identity)).json();
    for (const item of items) item.messages = await (await api(`/v1/sessions/${encodeURIComponent(item.id)}/messages`, {}, identity)).json();
    if (version === epoch) $("history").textContent = JSON.stringify(items, null, 2);
  } catch (error) { if (version === epoch) $("history").textContent = String(error); }
};
$("import").onclick = async () => {
  const version = epoch, identity = user();
  try { const result = await (await api(`/v1/sessions/${encodeURIComponent($("internal-session").value)}/files`, post({file_id: $("import-file").value}), identity)).json(); if (version === epoch) log(result); }
  catch (error) { if (version === epoch) log(String(error)); }
};
function showFile(record) {
  $("files").textContent = JSON.stringify(record, null, 2);
  if (record.download_url && /^https?:\/\//i.test(record.download_url)) {
    const link = document.createElement("a"); link.href = record.download_url; link.textContent = "Download"; link.target = "_blank"; link.rel = "noopener noreferrer"; $("files").append("\n", link);
  }
}
$("upload").onclick = async () => {
  const version = epoch, identity = user();
  try {
    const file = $("file").files[0]; if (!file) throw new Error("Select a file first");
    const data = new FormData(); data.append("file", file);
    const record = await (await api("/v1/files", {method: "POST", body: data}, identity)).json();
    const ids = filesByUser.get(identity) || new Set(); ids.add(record.id); filesByUser.set(identity, ids);
    if (version === epoch) { $("attachments").value = [...ids].join(", "); $("file-id").value = record.id; showFile(record); }
  } catch (error) { if (version === epoch) $("files").textContent = String(error); }
};
$("get-file").onclick = async () => {
  const version = epoch, identity = user();
  try { const record = await (await api(`/v1/files/${encodeURIComponent($("file-id").value)}`, {}, identity)).json(); if (version === epoch) showFile(record); }
  catch (error) { if (version === epoch) $("files").textContent = String(error); }
};
