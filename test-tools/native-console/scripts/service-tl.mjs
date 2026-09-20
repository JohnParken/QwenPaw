#!/usr/bin/env node
// Foreground local multi-user TL stack. Stops only children started here.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const consoleDir = path.join(root, "test-tools/native-console");
const proxyDir = path.join(root, "test-tools/tl-llm-proxy");
if (process.argv.includes("--help")) {
  console.log(
    "npm run service:tl — TL Proxy + real TL /v1 API/Worker (memory) + Native Console.\nOptional: QWENPAW_SERVICE_PORT=8092 QWENPAW_CONSOLE_PORT=5179 QWENPAW_TL_EXTERNAL=1 QWENPAW_SERVER_DEFINITION_PATH=...\nCtrl+C stops only this launch. Configure upstream in test-tools/tl-llm-proxy/.env.",
  );
  process.exit(0);
}
const port = Number(process.env.QWENPAW_SERVICE_PORT || 8092);
const uiPort = Number(process.env.QWENPAW_CONSOLE_PORT || 5179);
for (const value of [port, uiPort])
  if (!Number.isInteger(value) || value < 1 || value > 65535)
    throw new Error("Invalid listen port");
const python =
  process.env.QWENPAW_PYTHON ||
  path.join(
    root,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
const definition =
  process.env.QWENPAW_SERVER_DEFINITION_PATH ||
  path.join(root, "deploy/server/assistant.tl.local.json");
if (!existsSync(python) || !existsSync(definition))
  throw new Error("Python environment or platform definition is missing");
const token =
  process.env.QWENPAW_SERVER_SERVICE_TOKEN || randomBytes(32).toString("hex");
const env = {
  ...process.env,
  QWENPAW_SERVER_SERVICE_TOKEN: token,
  QWENPAW_SERVICE_TOKEN: token,
  QWENPAW_SERVICE_TARGET: `http://127.0.0.1:${port}`,
  QWENPAW_CHAT_MODE: "service",
};
const children = [];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  for (const child of children) child.kill("SIGTERM");
  const timer = setTimeout(() => {
    for (const child of children)
      if (child.exitCode === null) child.kill("SIGKILL");
  }, 5000);
  timer.unref();
}
process.on("SIGINT", () => stop());
process.on("SIGTERM", () => stop());
function start(command, args, cwd) {
  const child = spawn(command, args, { cwd, env, stdio: "inherit" });
  children.push(child);
  child.on("error", () => {
    console.error("Could not launch child service");
    stop(1);
  });
  child.on("exit", (code) => {
    if (!stopping) stop(code || 1);
  });
  return child;
}
async function free(port) {
  await new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", () =>
      reject(
        new Error(
          `Port ${port} is occupied; choose a different port. Existing services will not be stopped.`,
        ),
      ),
    );
    server.listen(port, "127.0.0.1", () => server.close(resolve));
  });
}
try {
  await free(port);
  await free(uiPort);
  if (process.env.QWENPAW_TL_EXTERNAL !== "1") {
    const entry = path.join(proxyDir, "dist/cli.js");
    if (!existsSync(entry) || !existsSync(path.join(proxyDir, ".env")))
      throw new Error(
        "Build tl-llm-proxy and configure its .env first; or set QWENPAW_TL_EXTERNAL=1 for an existing TL service.",
      );
    start(
      process.execPath,
      [entry, "--env-file", path.join(proxyDir, ".env")],
      proxyDir,
    );
  }
  start(
    python,
    [
      path.join(root, "scripts/server_tl_local.py"),
      "--definition",
      definition,
      "--port",
      String(port),
    ],
    root,
  );
  // Only probe readiness; never submit a paid model request at startup.
  const deadline = Date.now() + 30000;
  let ready = false;
  while (!stopping && Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/ready`, {
        signal: AbortSignal.timeout(1000),
      });
      if (response.ok) {
        ready = true;
        break;
      }
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (!ready || stopping)
    throw new Error("Local /v1 service did not become ready");
  start(
    process.execPath,
    [
      path.join(consoleDir, "node_modules/vite/bin/vite.js"),
      "--host",
      "127.0.0.1",
      "--port",
      String(uiPort),
    ],
    consoleDir,
  );
  console.log(
    `Multi-user TL console: http://127.0.0.1:${uiPort} — select Connect. Memory data is cleared on exit; chat uses the real configured TL model.`,
  );
} catch (error) {
  console.error(error.message);
  stop(1);
}
