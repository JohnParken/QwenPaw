// Single source of truth for mutable console state.
//
// The legacy page kept these as module-level `let` bindings inside a 2,200-line
// main.ts, which made every feature module reach into unrelated globals. They
// live here so any feature module can read/write one shared object instead.
//
// Modules must mutate `state.<field>` rather than rebinding it, because a
// re-assigned property on the exported object stays visible to all importers.

import type { ChatStream, RecordValue } from "../chat";
import type { ServiceSession } from "../service";

export interface LoadedFile {
  path: string;
  root: string;
  chatId: string;
  etag: string;
}

export interface ConsoleState {
  /** A `run()` task is in flight; blocks concurrent actions and session switches. */
  busy: boolean;
  streamController: AbortController | null;

  /** Local Console chat list plus the current selection. */
  chats: RecordValue[];
  currentChat: RecordValue | undefined;

  /** Multi-user `/v1` session list plus the current selection. */
  serviceSessions: ServiceSession[];
  currentServiceSession: ServiceSession | undefined;

  /** Workspace file editor state (ETag-guarded). */
  loadedFile: LoadedFile | null;
  nextCursor: string;
  listedDirectory: string;

  /** Channel config editor state. */
  loadedChannel: string;
  knownChannels: string[];

  /** In-flight assistant bubble wiring. */
  activeBubbleEl: HTMLElement | null;
  activeOutputPre: HTMLElement | null;
  activeStream: ChatStream | null;

  /** Tool-approval polling while a local run streams. */
  approvalPollTimer: ReturnType<typeof setInterval> | null;
  activeServiceRunId: string;
  serviceCancelRequested: boolean;
}

export const state: ConsoleState = {
  busy: false,
  streamController: null,

  chats: [],
  currentChat: undefined,

  serviceSessions: [],
  currentServiceSession: undefined,

  loadedFile: null,
  nextCursor: "",
  listedDirectory: "",

  loadedChannel: "",
  knownChannels: [],

  activeBubbleEl: null,
  activeOutputPre: null,
  activeStream: null,

  approvalPollTimer: null,
  activeServiceRunId: "",
  serviceCancelRequested: false,
};

/** Clears every session/file/config selection when switching mode or Agent. */
export function resetSelections(): void {
  state.chats = [];
  state.currentChat = undefined;
  state.serviceSessions = [];
  state.currentServiceSession = undefined;
  state.loadedFile = null;
  state.loadedChannel = "";
  state.nextCursor = "";
  state.listedDirectory = "";
  state.knownChannels = [];
}

export function stopApprovalPolling(): void {
  if (state.approvalPollTimer) {
    clearInterval(state.approvalPollTimer);
    state.approvalPollTimer = null;
  }
}

export function clearActiveStream(): void {
  state.streamController = null;
  state.activeStream = null;
  state.activeBubbleEl = null;
  state.activeOutputPre = null;
}
