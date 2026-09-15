#!/usr/bin/env node
// ==============================================================================
// QwenPaw 真实后端联调与服务环境跨平台管理引擎 (macOS & Windows 双系统原生支持)
// 零外部依赖，纯 Node.js 标准库实现
// 支持的操作:
//   - start-services : 启动底层依赖服务集群 (tl-proxy:8089 + qwenpaw:8088)
//   - start-console  : 启动方案 B 实战联调测试台 (native-console:5179)
//   - start-all      : 依次启动全流程三阶段服务 (tl-proxy -> qwenpaw -> native-console)
//   - stop           : 安全优雅停止全部服务并释放对应端口
// ==============================================================================

import { spawn, execSync } from "node:child_process";
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
  return {
    ...process.env,
    PATH: newPath,
  };
}

// 探测启动 Python 的命令环境（优先使用 uv 环境，执行 uv run python -m qwenpaw app --port 8088）
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
          args: ["run", "python", "-m", "qwenpaw", "app", "--port", "8088"],
          desc: `uv 环境 (${candidate} run python -m qwenpaw app --port 8088)`,
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
    args: ["run", "python", "-m", "qwenpaw", "app", "--port", "8088"],
    desc: "uv 环境 (uv run python -m qwenpaw app --port 8088)",
    fallback: fs.existsSync(venvPy)
      ? {
          cmd: venvPy,
          args: ["-m", "qwenpaw", "app", "--port", "8088"],
          desc: `虚拟环境 Python (${venvPy})`,
        }
      : {
          cmd: IS_WIN ? "python" : "python3",
          args: ["-m", "qwenpaw", "app", "--port", "8088"],
          desc: `系统 Python (${IS_WIN ? "python" : "python3"})`,
        },
  };
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
function spawnBackground(cmd, args, cwd, logFileName) {
  const logFilePath = path.join(LOG_DIR, logFileName);
  const out = fs.openSync(logFilePath, "a");
  const err = fs.openSync(logFilePath, "a");

  const proc = spawn(cmd, args, {
    cwd,
    detached: true,
    stdio: ["ignore", out, err],
    windowsHide: true,
    shell: IS_WIN,
    env: getExtendedEnv(),
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
  console.log("▶ [阶段 1] 检查并启动 tl-proxy 流量转发代理 (端口 8089)...");
  if (await checkPortInUse(8089)) {
    console.log("  ℹ 端口 8089 已在监听中，复用现有 tl-proxy 服务。");
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

  const ready = await waitForPort(8089, 15, "tl-proxy");
  if (!ready) {
    console.error(
      `❌ 错误: tl-proxy 未能在 15 秒内就绪，排查日志: ${path.join(LOG_DIR, "tl-proxy.log")}`,
    );
    return false;
  }
  console.log(`  ✓ tl-proxy 启动成功 (PID: ${pid}, 端口: 8089)`);
  return true;
}

async function startQwenPawBackend() {
  console.log("▶ [阶段 2] 检查并启动 QwenPaw Python 后端服务 (端口 8088)...");
  if (await checkPortInUse(8088)) {
    console.log("  ℹ 端口 8088 已在监听中，复用现有 QwenPaw 后端服务。");
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

  const ready = await waitForPort(8088, 45, "QwenPaw Python 后端");
  if (!ready) {
    console.error(
      `❌ 错误: QwenPaw 后端未能在 45 秒内就绪，排查日志: ${path.join(LOG_DIR, "qwenpaw.log")}`,
    );
    return false;
  }
  console.log(`  ✓ QwenPaw 后端启动成功 (PID: ${pid}, 端口: 8088)`);
  return true;
}

async function startNativeConsole() {
  console.log("▶ [阶段 3] 检查并启动 Native Console 前端测试台 (端口 5179)...");
  if (await checkPortInUse(5179)) {
    console.log("  ℹ 端口 5179 已在监听中，复用现有 Native Console 测试台。");
    return true;
  }

  const npmCmd = IS_WIN ? "npm.cmd" : "npm";
  const pid = spawnBackground(
    npmCmd,
    ["run", "dev"],
    CONSOLE_DIR,
    "native-console.log",
  );
  savePid("native-console", pid);

  const ready = await waitForPort(5179, 15, "Native Console");
  if (!ready) {
    console.error(
      `❌ 错误: Native Console 未能在 15 秒内就绪，排查日志: ${path.join(LOG_DIR, "native-console.log")}`,
    );
    return false;
  }
  console.log(
    `  ✓ Native Console 前端测试台启动成功 (PID: ${pid}, 端口: 5179)`,
  );
  return true;
}

// ------------------------------------------------------------------------------
// 顶层对外执行入口
// ------------------------------------------------------------------------------

async function main() {
  const action = process.argv[2] || "help";

  if (action === "start-services") {
    // 独立启动三阶段底层服务 (tl-proxy + qwenpaw)
    console.log("============================================================");
    console.log(
      `🚀 [基础服务集群] 正在启动 tl-proxy (8089) 与 QwenPaw (8088)... [${IS_WIN ? "Windows" : "macOS/Linux"}]`,
    );
    console.log("============================================================");
    const okTl = await startTlProxy();
    const okPaw = await startQwenPawBackend();
    console.log("");
    if (okTl && okPaw) {
      console.log(
        "============================================================",
      );
      console.log("🎉 底层服务集群已就绪！");
      console.log("  1. 流量转发代理 (tl-proxy): http://127.0.0.1:8089");
      console.log("  2. QwenPaw Python 后端服务: http://127.0.0.1:8088");
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
      `🧪 [方案 B 实战联调] 正在启动 Native Console 测试台 (5179)... [${IS_WIN ? "Windows" : "macOS/Linux"}]`,
    );
    console.log("============================================================");

    const hasTl = await checkPortInUse(8089);
    const hasPaw = await checkPortInUse(8088);

    if (!hasTl || !hasPaw) {
      console.log("⚠️ 提示: 检测到底层服务未完全启动：");
      if (!hasTl) console.log("   - tl-proxy (8089) 未就绪");
      if (!hasPaw) console.log("   - QwenPaw (8088) 未就绪");
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
      console.log("👉 请在浏览器中打开: http://127.0.0.1:5179");
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
      `🚀 [全链路三阶段] 正在依次启动所有联调测试服务... [${IS_WIN ? "Windows" : "macOS/Linux"}]`,
    );
    console.log("============================================================");
    const okTl = await startTlProxy();
    const okPaw = await startQwenPawBackend();
    const okConsole = await startNativeConsole();
    console.log("");
    if (okTl && okPaw && okConsole) {
      console.log(
        "============================================================",
      );
      console.log("🎉 全链路联调测试环境已全部就绪！");
      console.log("  1. 流量转发代理 (tl-proxy)   : http://127.0.0.1:8089");
      console.log("  2. QwenPaw Python 后端服务   : http://127.0.0.1:8088");
      console.log("  3. 测试台前端 (Native Console): http://127.0.0.1:5179");
      console.log("");
      console.log("👉 请在浏览器中打开: http://127.0.0.1:5179");
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
    cleanupPort(5179, "Native Console");
    removePid("native-console");

    // 2. QwenPaw 后端
    const pawPid = readPid("qwenpaw");
    await stopProcess(pawPid, "QwenPaw Python 后端");
    cleanupPort(8088, "QwenPaw Python 后端");
    removePid("qwenpaw");

    // 3. tl-proxy
    const tlPid = readPid("tl-proxy");
    await stopProcess(tlPid, "tl-proxy 流量转发代理");
    cleanupPort(8089, "tl-proxy 流量转发代理");
    removePid("tl-proxy");

    // 清理 run 目录
    try {
      if (fs.existsSync(PID_DIR)) fs.rmSync(PID_DIR, { recursive: true, force: true });
    } catch {}

    console.log("============================================================");
    console.log("🎉 所有服务已关闭，端口 5179, 8088, 8089 已完全释放！");
    console.log("============================================================");
  } else {
    console.log("用法: node scripts/manage-live.mjs <action>");
    console.log(
      "  start-services : 启动底层依赖服务集群 (tl-proxy:8089 + qwenpaw:8088)",
    );
    console.log(
      "  start-console  : 启动方案 B 实战联调测试台 (native-console:5179)",
    );
    console.log(
      "  start-all      : 依次启动全流程三阶段服务 (tl-proxy -> qwenpaw -> native-console)",
    );
    console.log("  stop           : 安全优雅停止全部服务并释放对应端口");
  }
}

main().catch((err) => {
  console.error("执行出错:", err);
  process.exit(1);
});
