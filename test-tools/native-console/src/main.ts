// Composition root.
//
// Every piece of behavior lives in a feature module; this file only builds the
// shared services, mounts the shell, wires the modules together in dependency
// order, and seeds the initial UI state.

import "./style.css";

import { ApiClient } from "./api";
import { CheckpointsPanel } from "./checkpoints";
import { ContextMonitor } from "./context-monitor";
import { InboxPanel } from "./inbox";
import { ModelLogStore } from "./model-log";
import { ServiceClient } from "./service";
import { SkillsPanel } from "./skills";
import { TLPanel } from "./tl";

import { run, setSanitizer } from "./core/app";
import { $ } from "./core/dom";
import { appShell } from "./shell";

import { bindChat } from "./features/chat";
import {
  bindConnect,
  modelDebugDefault,
  serviceUser,
} from "./features/connect";
import { bindFiles } from "./features/files";
import { bindLogPanels, modelDebugEnabled } from "./features/logs";
import { bindManagement, managementResult } from "./features/management";
import { bindSessions } from "./features/sessions";
import { bindTabs } from "./features/tabs";

// --- shared services --------------------------------------------------------

const api = new ApiClient();
const modelLogs = new ModelLogStore();
const service = new ServiceClient(api, {
  user: serviceUser,
  channel: "native-console",
});

setSanitizer((message) => String(api.safe(message)));

// --- mount ------------------------------------------------------------------

$("app").innerHTML = appShell;

// --- panels -----------------------------------------------------------------
// These render themselves into their mount points and call back into run().

const tl = new TLPanel(api, run, (data) => managementResult(api, data));
const contextMonitor = new ContextMonitor("context-monitor-root");
const inboxPanel = new InboxPanel(api, run, "inbox-mount");
const checkpointsPanel = new CheckpointsPanel(api, run, "checkpoints-mount");
const skillsPanel = new SkillsPanel(api, run, "skills-mount");

// --- feature modules --------------------------------------------------------

const logPanels = bindLogPanels(api, modelLogs);

const sessions = bindSessions({
  api,
  service,
  clearModelLogs: logPanels.clearModelLogs,
  loadCheckpoints: () => checkpointsPanel.loadCheckpoints(),
});

bindChat({
  api,
  service,
  tl,
  contextMonitor,
  debug: {
    modelDebugEnabled,
    captureModelLog: logPanels.captureModelLog,
  },
  sessions,
});

const connect = bindConnect({
  api,
  service,
  tl,
  contextMonitor,
  inboxPanel,
  skillsPanel,
  clearModelLogs: logPanels.clearModelLogs,
  refreshServiceSessions: (selected) =>
    sessions.refreshServiceSessions(selected),
});

bindManagement(api, tl);
bindFiles(api);
bindTabs({ inboxPanel, skillsPanel, checkpointsPanel });

// --- initial state ----------------------------------------------------------

if (modelDebugDefault) $<HTMLInputElement>("debug-toggle").checked = true;
api.setVerboseLogging(modelDebugEnabled());
connect.setChatModeUi(
  $<HTMLSelectElement>("chat-mode").value === "service" ? "service" : "local",
);
