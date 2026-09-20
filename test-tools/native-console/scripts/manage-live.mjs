#!/usr/bin/env node
// ==============================================================================
// QwenPaw 真实后端联调与服务环境跨平台管理引擎 (macOS & Windows 双系统原生支持)
// 零外部依赖，纯 Node.js 标准库实现
// 支持的操作:
//   - start-services : 启动底层依赖服务集群 (tl-proxy:8089 + qwenpaw:8088 + /v1:8092)
//   - start-console  : 启动方案 B 实战联调测试台 (native-console:5179)
//   - start-all      : 依次启动全流程四阶段服务 (tl-proxy -> qwenpaw -> /v1 -> native-console)
//   - stop           : 安全优雅停止全部服务并释放对应端口
// ==============================================================================

import { spawn, execSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import net from "node:net";
import os from "node:os";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CONSOLE_DIR = path.resolve(__dirname, "..");
const WORKSPACE_ROOT = path.resolve(CONSOLE_DIR, "../..");
const TL_PROXY_DIR = path.resolve(WORKSPACE_ROOT, "test-tools/tl-llm-proxy");
const PID_DIR = path.resolve(CONSOLE_DIR, ".run");
const LOG_DIR = path.resolve(CONSOLE_DIR, "logs");
const IS_WIN = os.platform() === "win32";

// 端口均可被环境变量覆盖，便于与已占用的默认端口并存
const TL_PROXY_PORT = Number(process.env.QWENPAW_TL_PROXY_PORT || 8089);
const PERSONAL_PORT = Number(process.env.QWENPAW_PERSONAL_PORT || 8088);
const CONSOLE_PORT = Number(process.env.QWENPAW_CONSOLE_PORT || 5179);

// 确保目录存在
if (!fs.existsSync(PID_DIR)) fs.mkdirSync(PID_DIR, { recursive: true });
if (!fs.existsSync(LOG_DIR)) fs.mkdirSync(LOG_DIR, { recursive: true });

// 探活端口是否已被占用
function checkPortInUse(port, host = "127.0.0.1", timeoutMs = 500) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let inUse = false;
    socket.setTimeout(timeoutMs);

    socket.once("connect", () => {
      inUse = true;
      socket.destroy();
    });
    socket.once("timeout", () => {
      socket.destroy();
    });
    socket.once("error", () => {
      resolve(false);
    });
    socket.once("close", () => {
      resolve(inUse);
    });

    socket.connect(port, host);
  });
}

// 轮询等待端口就绪
async function waitForPort(port, timeoutSec, name) {
  const startTime = Date.now();
  while ((Date.now() - startTime) / 1000 < timeoutSec) {
    if (await checkPortInUse(port)) {
      return true;
    }
    await new Promise((r) => setTimeout(r, 600));
  }
  return false;
}

// 获取扩展 PATH 的环境变量（确保能找到 uv、node、python）
function getExtendedEnv() {
  const home = os.homedir();
  const extraPaths = IS_WIN
    ? [
        path.join(home, ".cargo", "bin"),
        path.join(home, "AppData", "Local", "Programs", "uv"),
        path.join(home, ".local", "bin"),
      ]
    : [
        path.join(home, ".local", "bin"),
        path.join(home, ".cargo", "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
      ];
  const sep = path.delimiter;
  const currentPath = process.env.PATH || "";
  const newPath = [...extraPaths, currentPath].filter(Boolean).join(sep);
  const env = {
    ...process.env,
    PATH: newPath,
  };
  env.QWENPAW_MODEL_DEBUG ??= "1";
  env.QWENPAW_MODEL_DEBUG_DEFAULT ??= env.QWENPAW_MODEL_DEBUG;
  return env;
}

// 探测启动 Python 的命令环境（优先使用 uv 环境，执行 uv run python -m qwenpaw app）
function findPythonRunner() {
  const home = os.homedir();
  const uvBinary = IS_WIN ? "uv.exe" : "uv";
  const commonUvPaths = IS_WIN
    ? [
        path.join(home, ".cargo", "bin", "uv.exe"),
        path.join(home, "AppData", "Local", "Programs", "uv", "uv.exe"),
        path.join(home, ".local", "bin", "uv.exe"),
      ]
    : [
        path.join(home, ".local", "bin", "uv"),
        path.join(home, ".cargo", "bin", "uv"),
        "/opt/homebrew/bin/uv",
        "/usr/local/bin/uv",
      ];

  const env = getExtendedEnv();

  // 1. 检测系统中是否有 uv
  for (const candidate of [uvBinary, ...commonUvPaths]) {
    try {
      if (candidate === uvBinary || fs.existsSync(candidate)) {
        execSync(`"${candidate}" --version`, { stdio: "ignore", env });
        return {
          cmd: candidate,
          args: [
            "run",
            "python",
            "-m",
            "qwenpaw",
            "app",
            "--port",
            String(PERSONAL_PORT),
          ],
          desc: `uv 环境 (${candidate} run python -m qwenpaw app --port ${PERSONAL_PORT})`,
        };
      }
    } catch {}
  }

  // 2. 若直接探活被环境策略限制，默认以 uv 命令为准并配置 fallback 兜底
  const venvPy = IS_WIN
    ? path.join(WORKSPACE_ROOT, ".venv", "Scripts", "python.exe")
    : path.join(WORKSPACE_ROOT, ".venv", "bin", "python");

  return {
    cmd: "uv",
    args: [
      "run",
      "python",
      "-m",
      "qwenpaw",
      "app",
      "--port",
      String(PERSONAL_PORT),
    ],
    desc: `uv 环境 (uv run python -m qwenpaw app --port ${PERSONAL_PORT})`,
    fallback: fs.existsSync(venvPy)
      ? {
          cmd: venvPy,
          args: ["-m", "qwenpaw", "app", "--port", String(PERSONAL_PORT)],
          desc: `虚拟环境 Python (${venvPy})`,
        }
      : {
          cmd: IS_WIN ? "python" : "python3",
          args: ["-m", "qwenpaw", "app", "--port", String(PERSONAL_PORT)],
          desc: `系统 Python (${IS_WIN ? "python" : "python3"})`,
        },
  };
}

// 多用户 /v1 服务端口，可被环境变量覆盖
function servicePort() {
  return Number(process.env.QWENPAW_SERVICE_PORT || 8092);
}

// 读取控制台 .env.local 中的服务令牌（浏览器前端代理用它注入 /v1 请求）
function readConsoleServiceToken() {
  try {
    const file = path.join(CONSOLE_DIR, ".env.local");
    if (!fs.existsSync(file)) return "";
    for (const line of fs.readFileSync(file, "utf8").split("\n")) {
      const match = /^\s*QWENPAW_SERVICE_TOKEN\s*=\s*(.*)$/.exec(line);
      const value = match?.[1].trim().replace(/^["']|["']$/g, "");
      if (value) return value;
    }
  } catch {}
  return "";
}

// 后端脚本要求的 venv Python（scripts/server_tl_local.py 依赖完整服务依赖）
function findServicePython() {
  const candidates = IS_WIN
    ? [
        process.env.QWENPAW_PYTHON,
        path.join(WORKSPACE_ROOT, ".venv", "Scripts", "python.exe"),
      ]
    : [
        process.env.QWENPAW_PYTHON,
        path.join(WORKSPACE_ROOT, ".venv", "bin", "python"),
      ];
  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) return candidate;
  }
  return "";
}

// 探活 /ready，避免把“端口已占用”误判为 /v1 服务就绪
async function probeReady(port, deadlineMs) {
  const deadline = Date.now() + deadlineMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/ready`, {
        signal: AbortSignal.timeout(1000),
      });
      if (response.ok) return true;
    } catch {}
    await new Promise((r) => setTimeout(r, 600));
  }
  return false;
}

// 保存 PID
function savePid(serviceName, pid) {
  fs.writeFileSync(
    path.join(PID_DIR, `${serviceName}.pid`),
    String(pid).trim(),
    "utf8",
  );
}

// 读取 PID
function readPid(serviceName) {
  const file = path.join(PID_DIR, `${serviceName}.pid`);
  if (fs.existsSync(file)) {
    const content = fs.readFileSync(file, "utf8").trim();
    return Number(content) || null;
  }
  return null;
}

// 清理单个 PID 文件
function removePid(serviceName) {
  const file = path.join(PID_DIR, `${serviceName}.pid`);
  if (fs.existsSync(file)) fs.unlinkSync(file);
}

// 后台启动进程并将标准输出重定向到日志
function spawnBackground(cmd, args, cwd, logFileName, extraEnv = {}) {
  const logFilePath = path.join(LOG_DIR, logFileName);
  const out = fs.openSync(logFilePath, "a");
  const err = fs.openSync(logFilePath, "a");

  const proc = spawn(cmd, args, {
    cwd,
    detached: true,
    stdio: ["ignore", out, err],
    windowsHide: true,
    shell: IS_WIN,
    env: { ...getExtendedEnv(), ...extraEnv },
  });

  proc.unref();
  return proc.pid;
}

// 优雅停止进程
async function stopProcess(pid, name) {
  if (!pid) return;
  try {
    if (IS_WIN) {
      execSync(`taskkill /F /PID ${pid} /T`, { stdio: "ignore" });
      console.log(`  ✓ ${name} (PID: ${pid}) 已终止。`);
    } else {
      process.kill(pid, "SIGTERM");
      let exited = false;
      for (let i = 0; i < 5; i++) {
        await new Promise((r) => setTimeout(r, 600));
        try {
          process.kill(pid, 0);
        } catch {
          exited = true;
          break;
        }
      }
      if (!exited) {
        process.kill(pid, "SIGKILL");
      }
      console.log(`  ✓ ${name} (PID: ${pid}) 已停止。`);
    }
  } catch {
    // 进程已退出或无权限
  }
}

// 端口兜底深度清理
function cleanupPort(port, name) {
  try {
    if (IS_WIN) {
      const output = execSync(`netstat -ano | findstr :${port}`, {
        encoding: "utf8",
      });
      const lines = output.trim().split("\n");
      for (const line of lines) {
        const parts = line.trim().split(/\s+/);
        const pid = parts[parts.length - 1];
        if (pid && /^\d+$/.test(pid) && pid !== "0") {
          try {
            execSync(`taskkill /F /PID ${pid} /T`, { stdio: "ignore" });
          } catch {}
        }
      }
    } else {
      const pids = execSync(`lsof -ti :${port}`, { encoding: "utf8" }).trim();
      if (pids) {
        for (const p of pids.split("\n")) {
          if (p) {
            try {
              process.kill(Number(p), "SIGKILL");
            } catch {}
          }
        }
      }
    }
    console.log(`  ✓ 端口 ${port} (${name}) 检查与清理完成。`);
  } catch {
    console.log(`  ✓ 端口 ${port} (${name}) 无残留占用。`);
  }
}

// ------------------------------------------------------------------------------
// 各阶段具体启动方法
// ------------------------------------------------------------------------------

async function startTlProxy() {
  console.log(
    `▶ [阶段 1] 检查并启动 tl-proxy 流量转发代理 (端口 ${TL_PROXY_PORT})...`,
  );
  if (await checkPortInUse(TL_PROXY_PORT)) {
    console.log(
      `  ℹ 端口 ${TL_PROXY_PORT} 已在监听中，复用现有 tl-proxy 服务。`,
    );
    return true;
  }

  if (!fs.existsSync(TL_PROXY_DIR)) {
    console.log(`  ⚠️ 警告: 未找到目录 ${TL_PROXY_DIR}，跳过 tl-proxy。`);
    return false;
  }

  // 检查 .env
  const envFile = path.join(TL_PROXY_DIR, ".env");
  const envExample = path.join(TL_PROXY_DIR, ".env.example");
  if (!fs.existsSync(envFile) && fs.existsSync(envExample)) {
    console.log("  ℹ 自动从 .env.example 复制生成 .env...");
    fs.copyFileSync(envExample, envFile);
  }

  // 检查 dist/cli.js
  const cliJs = path.join(TL_PROXY_DIR, "dist", "cli.js");
  if (!fs.existsSync(cliJs)) {
    console.log("  ℹ 正在构建 tl-proxy TypeScript 产物...");
    execSync("npm run build", { cwd: TL_PROXY_DIR, stdio: "inherit" });
  }

  const proxyArgs = [cliJs];
  if (fs.existsSync(envFile)) {
    proxyArgs.push("--env-file", envFile);
  }

  const pid = spawnBackground(
    process.execPath,
    proxyArgs,
    TL_PROXY_DIR,
    "tl-proxy.log",
  );
  savePid("tl-proxy", pid);

  const ready = await waitForPort(TL_PROXY_PORT, 15, "tl-proxy");
  if (!ready) {
    console.error(
      `❌ 错误: tl-proxy 未能在 15 秒内就绪，排查日志: ${path.join(LOG_DIR, "tl-proxy.log")}`,
    );
    return false;
  }
  console.log(`  ✓ tl-proxy 启动成功 (PID: ${pid}, 端口: ${TL_PROXY_PORT})`);
  return true;
}

async function startQwenPawBackend() {
  console.log(
    `▶ [阶段 2] 检查并启动 QwenPaw Python 后端服务 (端口 ${PERSONAL_PORT})...`,
  );
  if (await checkPortInUse(PERSONAL_PORT)) {
    console.log(
      `  ℹ 端口 ${PERSONAL_PORT} 已在监听中，复用现有 QwenPaw 后端服务。`,
    );
    return true;
  }

  const runner = findPythonRunner();
  console.log(`  ℹ 使用 ${runner.desc}`);

  let pid;
  try {
    pid = spawnBackground(
      runner.cmd,
      runner.args,
      WORKSPACE_ROOT,
      "qwenpaw.log",
    );
  } catch (err) {
    if (runner.fallback) {
      console.log(`  ⚠️ 优先 uv 启动异常，降级使用: ${runner.fallback.desc}`);
      pid = spawnBackground(
        runner.fallback.cmd,
        runner.fallback.args,
        WORKSPACE_ROOT,
        "qwenpaw.log",
      );
    } else {
      throw err;
    }
  }
  savePid("qwenpaw", pid);

  const ready = await waitForPort(PERSONAL_PORT, 45, "QwenPaw Python 后端");
  if (!ready) {
    console.error(
      `❌ 错误: QwenPaw 后端未能在 45 秒内就绪，排查日志: ${path.join(LOG_DIR, "qwenpaw.log")}`,
    );
    return false;
  }
  console.log(`  ✓ QwenPaw 后端启动成功 (PID: ${pid}, 端口: ${PERSONAL_PORT})`);
  return true;
}

// 多用户 /v1 API/Worker（服务聊天入口）。缺少它时，浏览器只会看到开发代理
// 返回的 HTTP 500: ""，看起来像后端崩溃，实际是 8092 无人监听。
async function startServiceApi() {
  const port = servicePort();
  console.log(`▶ [阶段 3] 检查并启动多用户 /v1 服务 (端口 ${port})...`);
  if (await checkPortInUse(port)) {
    if (await probeReady(port, 3000)) {
      console.log(`  ℹ 端口 ${port} 已提供 /v1 服务，复用现有实例。`);
      return true;
    }
    console.error(
      `❌ 错误: 端口 ${port} 已被非 /v1 服务占用，服务聊天会失败；请释放该端口。`,
    );
    return false;
  }

  const python = findServicePython();
  if (!python) {
    console.error(
      "❌ 错误: 未找到 .venv Python；请先安装服务依赖，或设置 QWENPAW_PYTHON。",
    );
    return false;
  }
  const definition =
    process.env.QWENPAW_SERVER_DEFINITION_PATH ||
    path.join(WORKSPACE_ROOT, "deploy/server/assistant.tl.local.json");
  if (!fs.existsSync(definition)) {
    console.error(`❌ 错误: 未找到 /v1 助手定义: ${definition}`);
    return false;
  }

  // 前端代理在 Vite 启动时读取该令牌，因此两侧必须使用同一个值。
  const token =
    process.env.QWENPAW_SERVER_SERVICE_TOKEN ||
    readConsoleServiceToken() ||
    randomBytes(32).toString("hex");
  const serviceEnv = {
    QWENPAW_SERVER_SERVICE_TOKEN: token,
    QWENPAW_SERVICE_TOKEN: token,
    QWENPAW_SERVICE_TARGET: `http://127.0.0.1:${port}`,
    QWENPAW_CHAT_MODE: "service",
    QWENPAW_MODEL_DEBUG: process.env.QWENPAW_MODEL_DEBUG ?? "1",
  };
  // 让 startNativeConsole 复用同一份令牌/目标，避免两侧不一致导致 401。
  Object.assign(process.env, serviceEnv);

  const pid = spawnBackground(
    python,
    [
      path.join(WORKSPACE_ROOT, "scripts/server_tl_local.py"),
      "--definition",
      definition,
      "--port",
      String(port),
    ],
    WORKSPACE_ROOT,
    "service-api.log",
    serviceEnv,
  );
  savePid("service-api", pid);

  const ready = await probeReady(port, 45000);
  if (!ready) {
    console.error(
      `❌ 错误: /v1 服务未能在 45 秒内就绪，排查日志: ${path.join(LOG_DIR, "service-api.log")}`,
    );
    return false;
  }
  console.log(`  ✓ 多用户 /v1 服务启动成功 (PID: ${pid}, 端口: ${port})`);
  return true;
}

async function startNativeConsole() {
  console.log(
    `▶ [阶段 4] 检查并启动 Native Console 前端测试台 (端口 ${CONSOLE_PORT})...`,
  );
  if (await checkPortInUse(CONSOLE_PORT)) {
    console.log(
      `  ℹ 端口 ${CONSOLE_PORT} 已在监听中，复用现有 Native Console 测试台。`,
    );
    return true;
  }

  const npmCmd = IS_WIN ? "npm.cmd" : "npm";
  // 前端代理在 Vite 启动时固定 /v1 目标与令牌，这里显式注入，避免与
  // .env.local 或环境变量不一致导致 401。
  const consoleEnv = {
    QWENPAW_SERVICE_TARGET: `http://127.0.0.1:${servicePort()}`,
    QWENPAW_SERVICE_TOKEN:
      process.env.QWENPAW_SERVICE_TOKEN || readConsoleServiceToken(),
    QWENPAW_CHAT_MODE: "service",
    QWENPAW_CONSOLE_PORT: String(CONSOLE_PORT),
  };
  const pid = spawnBackground(
    npmCmd,
    ["run", "dev"],
    CONSOLE_DIR,
    "native-console.log",
    consoleEnv,
  );
  savePid("native-console", pid);

  const ready = await waitForPort(CONSOLE_PORT, 15, "Native Console");
  if (!ready) {
    console.error(
      `❌ 错误: Native Console 未能在 15 秒内就绪，排查日志: ${path.join(LOG_DIR, "native-console.log")}`,
    );
    return false;
  }
  console.log(
    `  ✓ Native Console 前端测试台启动成功 (PID: ${pid}, 端口: ${CONSOLE_PORT})`,
  );
  return true;
}

// ------------------------------------------------------------------------------
// 顶层对外执行入口
// ------------------------------------------------------------------------------

async function main() {
  const action = process.argv[2] || "help";

  if (action === "start-services") {
    // 独立启动底层服务 (tl-proxy + qwenpaw + /v1)
    console.log("============================================================");
    console.log(
      `🚀 [基础服务集群] 正在启动 tl-proxy (${TL_PROXY_PORT})、QwenPaw (${PERSONAL_PORT}) 与 /v1 (${servicePort()})... [${IS_WIN ? "Windows" : "macOS/Linux"}]`,
    );
    console.log("============================================================");
    const okTl = await startTlProxy();
    const okPaw = await startQwenPawBackend();
    const okApi = await startServiceApi();
    console.log("");
    if (okTl && okPaw && okApi) {
      console.log(
        "============================================================",
      );
      console.log("🎉 底层服务集群已就绪！");
      console.log(
        `  1. 流量转发代理 (tl-proxy): http://127.0.0.1:${TL_PROXY_PORT}`,
      );
      console.log(
        `  2. QwenPaw Python 后端服务: http://127.0.0.1:${PERSONAL_PORT}`,
      );
      console.log(
        `  3. 多用户 /v1 服务 (服务聊天): http://127.0.0.1:${servicePort()}`,
      );
      console.log(
        "👉 现在可以执行方案 B 实战联调: npm run live:console 或运行 start-console 脚本",
      );
      console.log(
        "============================================================",
      );
    } else {
      process.exit(1);
    }
  } else if (action === "start-console") {
    // 独立启动方案 B 实战联调测试台 (native-console)
    console.log("============================================================");
    console.log(
      `🧪 [方案 B 实战联调] 正在启动 Native Console 测试台 (${CONSOLE_PORT})... [${IS_WIN ? "Windows" : "macOS/Linux"}]`,
    );
    console.log("============================================================");

    const hasTl = await checkPortInUse(TL_PROXY_PORT);
    const hasPaw = await checkPortInUse(PERSONAL_PORT);
    const hasApi = await probeReady(servicePort(), 2000);

    if (!hasTl || !hasPaw || !hasApi) {
      console.log("⚠️ 提示: 检测到底层服务未完全启动：");
      if (!hasTl) console.log(`   - tl-proxy (${TL_PROXY_PORT}) 未就绪`);
      if (!hasPaw) console.log(`   - QwenPaw (${PERSONAL_PORT}) 未就绪`);
      if (!hasApi)
        console.log(
          `   - 多用户 /v1 服务 (${servicePort()}) 未就绪；「服务聊天 /v1」会收到 HTTP 500: ""`,
        );
      console.log(
        "💡 建议先执行 npm run live:services 启动基础服务，或直接使用 npm run live:start 一键启动全部。",
      );
      console.log("");
    }

    const okConsole = await startNativeConsole();
    console.log("");
    if (okConsole) {
      console.log(
        "============================================================",
      );
      console.log("🎉 方案 B 实战联调测试台已就绪！");
      console.log(`👉 请在浏览器中打开: http://127.0.0.1:${CONSOLE_PORT}`);
      console.log("📜 实时日志查看:");
      console.log(
        `   - Native Console: tail -f ${path.join(LOG_DIR, "native-console.log")}`,
      );
      console.log(
        "============================================================",
      );
    } else {
      process.exit(1);
    }
  } else if (action === "start-all") {
    // 全量一次性启动
    console.log("============================================================");
    console.log(
      `🚀 [全链路四阶段] 正在依次启动所有联调测试服务... [${IS_WIN ? "Windows" : "macOS/Linux"}]`,
    );
    console.log("============================================================");
    const okTl = await startTlProxy();
    const okPaw = await startQwenPawBackend();
    const okApi = await startServiceApi();
    const okConsole = await startNativeConsole();
    console.log("");
    if (okTl && okPaw && okApi && okConsole) {
      console.log(
        "============================================================",
      );
      console.log("🎉 全链路联调测试环境已全部就绪！");
      console.log(
        `  1. 流量转发代理 (tl-proxy)   : http://127.0.0.1:${TL_PROXY_PORT}`,
      );
      console.log(
        `  2. QwenPaw Python 后端服务   : http://127.0.0.1:${PERSONAL_PORT}`,
      );
      console.log(
        `  3. 多用户 /v1 服务 (服务聊天) : http://127.0.0.1:${servicePort()}`,
      );
      console.log(
        `  4. 测试台前端 (Native Console): http://127.0.0.1:${CONSOLE_PORT}`,
      );
      console.log("");
      console.log(`👉 请在浏览器中打开: http://127.0.0.1:${CONSOLE_PORT}`);
      console.log(
        "💬 「服务聊天 /v1」由第 3 项提供；连接失败时先看该服务的日志。",
      );
      console.log("🛑 停止全部服务请运行: npm run live:stop (或 stop 脚本)");
      console.log(
        "============================================================",
      );
    } else {
      process.exit(1);
    }
  } else if (action === "stop") {
    console.log("============================================================");
    console.log(
      `🛑 正在安全关闭全部联调测试服务... [${IS_WIN ? "Windows" : "macOS/Linux"}]`,
    );
    console.log("============================================================");

    // 1. Native Console
    const consolePid = readPid("native-console");
    await stopProcess(consolePid, "Native Console 测试台");
    cleanupPort(CONSOLE_PORT, "Native Console");
    removePid("native-console");

    // 2. QwenPaw 后端
    const pawPid = readPid("qwenpaw");
    await stopProcess(pawPid, "QwenPaw Python 后端");
    cleanupPort(PERSONAL_PORT, "QwenPaw Python 后端");
    removePid("qwenpaw");

    // 3. 多用户 /v1 服务
    const apiPid = readPid("service-api");
    await stopProcess(apiPid, "多用户 /v1 服务");
    cleanupPort(servicePort(), "多用户 /v1 服务");
    removePid("service-api");

    // 4. tl-proxy
    const tlPid = readPid("tl-proxy");
    await stopProcess(tlPid, "tl-proxy 流量转发代理");
    cleanupPort(TL_PROXY_PORT, "tl-proxy 流量转发代理");
    removePid("tl-proxy");

    // 清理 run 目录
    try {
      if (fs.existsSync(PID_DIR))
        fs.rmSync(PID_DIR, { recursive: true, force: true });
    } catch {}

    console.log("============================================================");
    console.log(
      `🎉 所有服务已关闭，端口 ${CONSOLE_PORT}, ${PERSONAL_PORT}, ${servicePort()}, ${TL_PROXY_PORT} 已完全释放！`,
    );
    console.log("============================================================");
  } else {
    console.log("用法: node scripts/manage-live.mjs <action>");
    console.log(
      "  start-services : 启动底层依赖服务集群 (tl-proxy:8089 + qwenpaw:8088 + /v1:8092)",
    );
    console.log(
      "  start-console  : 启动方案 B 实战联调测试台 (native-console:5179)",
    );
    console.log(
      "  start-all      : 依次启动全流程四阶段服务 (tl-proxy -> qwenpaw -> /v1 -> native-console)",
    );
    console.log("  stop           : 安全优雅停止全部服务并释放对应端口");
  }
}

main().catch((err) => {
  console.error("执行出错:", err);
  process.exit(1);
});
