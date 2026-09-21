# QwenPaw 主服务 API 参考（`/api`）

本文件由运行中的后端 OpenAPI 描述生成，覆盖 QwenPaw 个人后端（默认 `http://127.0.0.1:8088`）
挂在 `/api` 下的 HTTP 接口。生成方式见文末。

多用户服务 `/v1` 是另一套接口，见 [Service v1 API](v1-api.md)；两者不要混用。

## 约定

- **前缀**：下表路径除 `/`、`/console` 外均已是完整路径，直接请求 `http://127.0.0.1:8088<路径>`。
- **鉴权**：由后端 `/api/auth/status` 决定是否启用；启用后需带 `Authorization: Bearer <token>`。
- **Agent 作用域**：多数业务接口同时挂在 `/api/agents/{agentId}/...` 下（共 231 个操作），见下文「Agent 作用域镜像」。
- **流式**：聊天类接口返回 `text/event-stream`（SSE）。

共 **386** 个顶层操作，**231** 个 Agent 作用域操作。

## Agent 管理（`agents`，4）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/agents` | List all agents |
| `POST` | `/api/agents` | Create new agent |
| `GET` | `/api/agents/memory/backends` | List Memory Backends |
| `PUT` | `/api/agents/order` | Persist agent order |

## Agent 统计（`agent-stats`，2）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/agent-stats` | Get agent statistics summary |
| `GET` | `/api/agent-stats/llm-tool-trend` | Global LLM and tool-call trend |

## 会话与消息（`chats`，22）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/chats` | List Chats |
| `POST` | `/api/chats` | Create Chat |
| `POST` | `/api/chats/actions/batch-archive` | Batch Archive Chats |
| `POST` | `/api/chats/actions/batch-unarchive` | Batch Unarchive Chats |
| `POST` | `/api/chats/batch-delete` | Batch Delete Chats |
| `GET` | `/api/chats/groups` | List Chat Groups |
| `POST` | `/api/chats/groups` | Create Chat Group |
| `PUT` | `/api/chats/groups/order` | Reorder Chat Groups |
| `DELETE` | `/api/chats/groups/{group_id}` | Delete Chat Group |
| `PUT` | `/api/chats/groups/{group_id}` | Update Chat Group |
| `DELETE` | `/api/chats/{chat_id}` | Delete Chat |
| `GET` | `/api/chats/{chat_id}` | Get Chat |
| `PUT` | `/api/chats/{chat_id}` | Update Chat |
| `POST` | `/api/chats/{chat_id}/archive` | Archive Chat |
| `DELETE` | `/api/chats/{chat_id}/project-dir` | Clear Chat Project Dir |
| `GET` | `/api/chats/{chat_id}/project-dir` | Get Chat Project Dir |
| `PUT` | `/api/chats/{chat_id}/project-dir` | Set Chat Project Dir |
| `DELETE` | `/api/chats/{chat_id}/project-dirs` | Clear Chat Project Dirs |
| `GET` | `/api/chats/{chat_id}/project-dirs` | Get Chat Project Dirs |
| `PUT` | `/api/chats/{chat_id}/project-dirs` | Set Chat Project Dirs |
| `GET` | `/api/chats/{chat_id}/status` | Get Chat Status |
| `POST` | `/api/chats/{chat_id}/unarchive` | Unarchive Chat |

## 消息（`messages`，1）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/messages/send` | Send Message |

## Console 聊天与收件箱（`console`，12）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/console/attachments/parse` | Parse Console Attachment |
| `POST` | `/api/console/chat` | Chat with console (streaming response) |
| `POST` | `/api/console/chat/stop` | Stop running console chat |
| `POST` | `/api/console/chat/task` | Submit a background chat task |
| `GET` | `/api/console/chat/task/{task_id}` | Check background chat task status |
| `GET` | `/api/console/debug/backend-logs` | Read backend daemon logs for debug page |
| `GET` | `/api/console/inbox/events` | Get Inbox Events |
| `DELETE` | `/api/console/inbox/events/{event_id}` | Delete Inbox Event |
| `POST` | `/api/console/inbox/read` | Post Mark Inbox Read |
| `GET` | `/api/console/inbox/traces/{run_id}` | Get Inbox Trace |
| `GET` | `/api/console/push-messages` | Get Push Messages |
| `POST` | `/api/console/upload` | Upload file for chat |

## 工具审批（`approval`，3）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/approval/approve` | Approve a pending tool execution |
| `POST` | `/api/approval/deny` | Deny a pending tool execution |
| `GET` | `/api/approval/list` | List pending approval requests |

## 工具调用（`tool-calls`，7）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/tool-calls/{session_id}` | List Calls |
| `GET` | `/api/tool-calls/{session_id}/{tool_call_id}` | Get Call |
| `POST` | `/api/tool-calls/{session_id}/{tool_call_id}/cancel` | Cancel Call |
| `POST` | `/api/tool-calls/{session_id}/{tool_call_id}/extend-deadline` | Extend Deadline |
| `POST` | `/api/tool-calls/{session_id}/{tool_call_id}/offload` | Offload Call |
| `GET` | `/api/tool-calls/{session_id}/{tool_call_id}/output` | Get Output |
| `GET` | `/api/tool-calls/{session_id}/{tool_call_id}/stream` | Stream Output |

## 工具（`tools`，5）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/tools` | List Tools |
| `PATCH` | `/api/tools/{tool_name}/async-execution` | Update Tool Async Execution |
| `GET` | `/api/tools/{tool_name}/config` | Get Tool Config |
| `POST` | `/api/tools/{tool_name}/config` | Update Tool Config |
| `PATCH` | `/api/tools/{tool_name}/toggle` | Toggle Tool |

## 技能（`skills`，47）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/skills` | List Skills |
| `POST` | `/api/skills` | Create Skill |
| `POST` | `/api/skills/ai/optimize/stream` | Ai Optimize Skill Stream |
| `POST` | `/api/skills/batch-delete` | Batch Delete Skills |
| `POST` | `/api/skills/batch-disable` | Batch Disable Skills |
| `POST` | `/api/skills/batch-enable` | Batch Enable Skills |
| `POST` | `/api/skills/hub/install/cancel/{task_id}` | Cancel Hub Install |
| `POST` | `/api/skills/hub/install/start` | Start Install From Hub |
| `GET` | `/api/skills/hub/install/status/{task_id}` | Get Hub Install Status |
| `GET` | `/api/skills/hub/search` | Search Hub |
| `GET` | `/api/skills/pool` | List Pool Skills |
| `POST` | `/api/skills/pool/batch-delete` | Batch Delete Pool Skills |
| `GET` | `/api/skills/pool/builtin-notice` | Get Pool Builtin Notice |
| `GET` | `/api/skills/pool/builtin-sources` | List Pool Builtin Sources |
| `POST` | `/api/skills/pool/create` | Create Pool Skill |
| `POST` | `/api/skills/pool/download` | Download Pool Skill To Workspaces |
| `POST` | `/api/skills/pool/import` | Import Skill Pool From Hub |
| `POST` | `/api/skills/pool/import-builtin` | Import Pool Builtins |
| `POST` | `/api/skills/pool/refresh` | Refresh Pool Skills |
| `PUT` | `/api/skills/pool/save` | Save Pool Skill |
| `POST` | `/api/skills/pool/upload` | Upload Workspace Skill To Pool |
| `POST` | `/api/skills/pool/upload-zip` | Upload Skill Pool Zip |
| `DELETE` | `/api/skills/pool/{skill_name}` | Delete Pool Skill |
| `GET` | `/api/skills/pool/{skill_name}` | Get Pool Skill |
| `PUT` | `/api/skills/pool/{skill_name}/auto-sync` | Update Pool Skill Auto Sync |
| `PUT` | `/api/skills/pool/{skill_name}/auto-update` | Update Pool Skill Auto Sync |
| `PUT` | `/api/skills/pool/{skill_name}/automation` | Update Pool Skill Automation |
| `DELETE` | `/api/skills/pool/{skill_name}/config` | Delete Pool Skill Config |
| `GET` | `/api/skills/pool/{skill_name}/config` | Get Pool Skill Config |
| `PUT` | `/api/skills/pool/{skill_name}/config` | Update Pool Skill Config |
| `PUT` | `/api/skills/pool/{skill_name}/tags` | Update Pool Skill Tags |
| `POST` | `/api/skills/pool/{skill_name}/update-builtin` | Update Pool Builtin |
| `POST` | `/api/skills/refresh` | Refresh Skills |
| `PUT` | `/api/skills/save` | Save Workspace Skill |
| `POST` | `/api/skills/upload` | Upload Skill Zip |
| `GET` | `/api/skills/workspaces` | List Workspace Skill Sources |
| `DELETE` | `/api/skills/{skill_name}` | Delete Skill |
| `GET` | `/api/skills/{skill_name}` | Get Skill |
| `PUT` | `/api/skills/{skill_name}/channels` | Update Skill Channels Endpoint |
| `DELETE` | `/api/skills/{skill_name}/config` | Delete Skill Config Endpoint |
| `GET` | `/api/skills/{skill_name}/config` | Get Skill Config Endpoint |
| `PUT` | `/api/skills/{skill_name}/config` | Update Skill Config Endpoint |
| `POST` | `/api/skills/{skill_name}/disable` | Disable Skill |
| `POST` | `/api/skills/{skill_name}/enable` | Enable Skill |
| `GET` | `/api/skills/{skill_name}/files/{file_path}` | Load Skill File |
| `PUT` | `/api/skills/{skill_name}/preload` | Update Skill Preload Endpoint |
| `PUT` | `/api/skills/{skill_name}/tags` | Update Skill Tags |

## MCP（`mcp`，11）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/mcp` | List all MCP clients |
| `POST` | `/api/mcp` | Create a new MCP client |
| `GET` | `/api/mcp/access-principals` | List recent source-scoped principals for MCP access rules |
| `GET` | `/api/mcp/policy/{client_key}` | Get saved MCP access policy |
| `PUT` | `/api/mcp/policy/{client_key}` | Update saved MCP access policy |
| `PATCH` | `/api/mcp/toggle/{client_key}` | Toggle MCP client enabled status |
| `GET` | `/api/mcp/tools/{client_key}` | List tools from a connected MCP server |
| `PUT` | `/api/mcp/tools/{client_key}` | Update tool whitelist for an MCP client |
| `DELETE` | `/api/mcp/{client_key}` | Delete an MCP client |
| `GET` | `/api/mcp/{client_key}` | Get MCP client details |
| `PUT` | `/api/mcp/{client_key}` | Update an MCP client |

## MCP OAuth（`mcp-oauth`，4）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/mcp/oauth/callback` | Oauth Callback |
| `POST` | `/api/mcp/oauth/start/{client_key}` | Oauth Start |
| `GET` | `/api/mcp/oauth/status/{client_key}` | Oauth Status |
| `DELETE` | `/api/mcp/oauth/{client_key}` | Oauth Revoke |

## 模型与 Provider（`models`，17）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/models` | List all providers |
| `GET` | `/api/models/active` | Get effective active LLM |
| `PUT` | `/api/models/active` | Set active LLM |
| `POST` | `/api/models/custom-providers` | Create a custom provider |
| `DELETE` | `/api/models/custom-providers/{provider_id}` | Delete a custom provider |
| `POST` | `/api/models/openrouter/discover-extended` | Discover OpenRouter models with extended metadata |
| `POST` | `/api/models/openrouter/models/filter` | Filter OpenRouter models by criteria |
| `GET` | `/api/models/openrouter/series` | Get available OpenRouter provider series |
| `PUT` | `/api/models/{provider_id}/config` | Configure a provider |
| `POST` | `/api/models/{provider_id}/discover` | Discover available models from provider |
| `POST` | `/api/models/{provider_id}/models` | Add a model to a provider |
| `POST` | `/api/models/{provider_id}/models/test` | Test a specific model |
| `DELETE` | `/api/models/{provider_id}/models/{model_id}` | Remove a model from a provider |
| `PUT` | `/api/models/{provider_id}/models/{model_id}/config` | Configure per-model generation parameters |
| `POST` | `/api/models/{provider_id}/models/{model_id}/probe-multimodal` | Probe model multimodal capability |
| `PUT` | `/api/models/{provider_id}/models/{model_id}/visibility` | Hide or restore a discovered model |
| `POST` | `/api/models/{provider_id}/test` | Test provider connection |

## Provider OAuth（`provider-oauth`，3）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/providers/{provider_id}/oauth/callback` | OAuth callback (redirect target) |
| `POST` | `/api/providers/{provider_id}/oauth/start` | Start OAuth flow for a provider |
| `GET` | `/api/providers/{provider_id}/oauth/status` | Poll OAuth flow status |

## 本地模型（`local-models`，14）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/local-models/config` | Get local model settings |
| `PUT` | `/api/local-models/config` | Configure local model settings |
| `GET` | `/api/local-models/models` | List recommended and downloaded local models |
| `DELETE` | `/api/local-models/models/download` | Cancel local model download |
| `GET` | `/api/local-models/models/download` | Get local model download progress |
| `POST` | `/api/local-models/models/download` | Start local model download |
| `DELETE` | `/api/local-models/models/{model_id}` | Delete a downloaded local model |
| `DELETE` | `/api/local-models/server` | Stop llama.cpp server |
| `GET` | `/api/local-models/server` | Check if local server is available |
| `POST` | `/api/local-models/server` | Start llama.cpp server |
| `DELETE` | `/api/local-models/server/download` | Cancel llama.cpp download |
| `GET` | `/api/local-models/server/download` | Get llama.cpp download progress |
| `POST` | `/api/local-models/server/download` | Start llama.cpp download |
| `GET` | `/api/local-models/server/update` | Check if a llama.cpp update is available |

## Token 统计（`token-usage`，2）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/token-usage` | Get token usage summary |
| `GET` | `/api/token-usage/details` | Get token usage details |

## 工作区（`workspace`，36）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/workspace/audio-mode` | Get audio mode |
| `PUT` | `/api/workspace/audio-mode` | Update audio mode |
| `GET` | `/api/workspace/binary-files/{file_path}` | Serve a binary workspace file (images, PDFs) for preview |
| `GET` | `/api/workspace/code-files` | List all workspace files (Coding Mode) |
| `GET` | `/api/workspace/code-files/{file_path}` | Read any workspace file (Coding Mode) |
| `PUT` | `/api/workspace/code-files/{file_path}` | Write any workspace file (Coding Mode) |
| `GET` | `/api/workspace/commands/available` | Get Available Commands |
| `GET` | `/api/workspace/download` | Download workspace as zip |
| `POST` | `/api/workspace/embedding/test` | Test embedding configuration |
| `GET` | `/api/workspace/file-content` | Read a bounded workspace text chunk |
| `PUT` | `/api/workspace/file-content` | Save workspace text with optimistic concurrency |
| `GET` | `/api/workspace/file-download` | Stream one workspace file |
| `GET` | `/api/workspace/file-metadata` | Read workspace file metadata |
| `POST` | `/api/workspace/file-upload` | Stream ordinary files into one workspace directory |
| `GET` | `/api/workspace/files` | List working files |
| `GET` | `/api/workspace/files/{md_name}` | Read a working file |
| `PUT` | `/api/workspace/files/{md_name}` | Write a working file |
| `GET` | `/api/workspace/html-file-uri` | Resolve one workspace HTML file for the desktop browser |
| `GET` | `/api/workspace/language` | Get agent language |
| `PUT` | `/api/workspace/language` | Update agent language |
| `GET` | `/api/workspace/local-whisper-status` | Check local whisper availability |
| `GET` | `/api/workspace/memory` | List memory files |
| `GET` | `/api/workspace/memory/{md_path}` | Read a memory file |
| `PUT` | `/api/workspace/memory/{md_path}` | Write a memory file |
| `GET` | `/api/workspace/running-config` | Get agent running config |
| `PUT` | `/api/workspace/running-config` | Update agent running config |
| `GET` | `/api/workspace/system-prompt-files` | Get system prompt files |
| `PUT` | `/api/workspace/system-prompt-files` | Update system prompt files |
| `POST` | `/api/workspace/transcribe` | Transcribe audio to text |
| `PUT` | `/api/workspace/transcription-provider` | Set transcription provider |
| `GET` | `/api/workspace/transcription-provider-type` | Get transcription provider type |
| `PUT` | `/api/workspace/transcription-provider-type` | Set transcription provider type |
| `GET` | `/api/workspace/transcription-providers` | List transcription providers |
| `GET` | `/api/workspace/tree` | List one workspace directory page |
| `POST` | `/api/workspace/upload` | Upload zip and merge into workspace |
| `GET` | `/api/workspace/watch` | SSE stream for agent workspace file changes |

## 文件（`files`，2）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/files/preview/{filepath}` | Preview file |
| `HEAD` | `/api/files/preview/{filepath}` | Preview file |

## 检查点（`checkpoints`，11）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `DELETE` | `/api/workspace/checkpoints` | Reset Checkpoints |
| `PATCH` | `/api/workspace/checkpoints/auto` | Set Checkpoint Auto |
| `POST` | `/api/workspace/checkpoints/gc` | Apply Checkpoint Gc |
| `POST` | `/api/workspace/checkpoints/gc/preview` | Preview Checkpoint Gc |
| `GET` | `/api/workspace/checkpoints/gc/settings` | Get Checkpoint Gc Settings |
| `PATCH` | `/api/workspace/checkpoints/gc/settings` | Update Checkpoint Gc Settings |
| `GET` | `/api/workspace/checkpoints/graph` | Checkpoint Graph |
| `POST` | `/api/workspace/checkpoints/restore` | Apply Checkpoint Restore |
| `POST` | `/api/workspace/checkpoints/restore/preview` | Preview Checkpoint Restore |
| `POST` | `/api/workspace/checkpoints/snapshot` | Create Checkpoint |
| `GET` | `/api/workspace/checkpoints/status` | Checkpoint Status |

## Git（`git`，11）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/workspace/git/branches` | List branches |
| `POST` | `/api/workspace/git/checkout` | Switch (or create) branch |
| `POST` | `/api/workspace/git/commit` | Commit staged changes |
| `GET` | `/api/workspace/git/commit-diff` | Show diff introduced by a commit |
| `GET` | `/api/workspace/git/diff` | Get diff for a file (or full staged/unstaged diff) |
| `POST` | `/api/workspace/git/discard` | Discard working-directory changes |
| `GET` | `/api/workspace/git/log` | Recent commit log |
| `POST` | `/api/workspace/git/revert` | Revert a commit (creates a new revert commit) |
| `POST` | `/api/workspace/git/stage` | Stage files (git add) |
| `GET` | `/api/workspace/git/status` | Git status |
| `POST` | `/api/workspace/git/unstage` | Unstage files (git restore --staged) |

## 项目目录（`project-directory`，9）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/workspace/project-directory` | Get the agent default project directory |
| `PUT` | `/api/workspace/project-directory` | Set (or clear) the active project directory directory |
| `GET` | `/api/workspace/project-directory/browse-dirs` | Browse directories on the server for project selection |
| `POST` | `/api/workspace/project-directory/browse-dirs/create` | Create a child directory in the directory browser |
| `POST` | `/api/workspace/project-directory/clone` | Clone a public GitHub/Git repository (SSE progress) |
| `POST` | `/api/workspace/project-directory/create` | Create a new empty project directory |
| `POST` | `/api/workspace/project-directory/import-local` | Copy a local directory into project directorys |
| `GET` | `/api/workspace/project-directory/list` | List all project directorys for this agent |
| `POST` | `/api/workspace/project-directory/upload-zip` | Upload a zip of a project folder to coding_projects/ |

## 环境变量（`envs`，6）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/envs` | List Envs |
| `PATCH` | `/api/envs` | Patch Envs |
| `PUT` | `/api/envs` | Batch Save Envs |
| `GET` | `/api/envs/catalog` | List Env Catalog |
| `DELETE` | `/api/envs/{key}` | Delete Env |
| `POST` | `/api/envs/{key}/reset` | Reset Env |

## 定时任务（`cron`，12）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/cron/dispatch-targets` | List Dispatch Targets |
| `GET` | `/api/cron/jobs` | List Jobs |
| `POST` | `/api/cron/jobs` | Create Job |
| `DELETE` | `/api/cron/jobs/{job_id}` | Delete Job |
| `GET` | `/api/cron/jobs/{job_id}` | Get Job |
| `PUT` | `/api/cron/jobs/{job_id}` | Replace Job |
| `GET` | `/api/cron/jobs/{job_id}/history` | Get Job History |
| `POST` | `/api/cron/jobs/{job_id}/pause` | Pause Job |
| `POST` | `/api/cron/jobs/{job_id}/promote` | Promote Imported Job |
| `POST` | `/api/cron/jobs/{job_id}/resume` | Resume Job |
| `POST` | `/api/cron/jobs/{job_id}/run` | Run Job |
| `GET` | `/api/cron/jobs/{job_id}/state` | Get Job State |

## 配置（`config`，42）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/config/acp` | Get ACP config |
| `PUT` | `/api/config/acp` | Update ACP config |
| `GET` | `/api/config/acp/node-runtime` | Get ACP Node runtime |
| `PUT` | `/api/config/acp/node-runtime` | Update ACP Node runtime |
| `GET` | `/api/config/acp/{agent_name}` | Get ACP agent config |
| `PUT` | `/api/config/acp/{agent_name}` | Update ACP agent config |
| `GET` | `/api/config/agents/llm-routing` | Get agent LLM routing settings |
| `PUT` | `/api/config/agents/llm-routing` | Update agent LLM routing settings |
| `GET` | `/api/config/channels` | List all channels |
| `PUT` | `/api/config/channels` | Update all channels |
| `GET` | `/api/config/channels/schemas` | Get plugin channel config schemas |
| `GET` | `/api/config/channels/types` | List channel types |
| `GET` | `/api/config/channels/{channel_name}` | Get channel config |
| `PUT` | `/api/config/channels/{channel_name}` | Update channel config |
| `POST` | `/api/config/channels/{channel_name}/conflict-check` | Check channel Bot conflicts |
| `GET` | `/api/config/channels/{channel_name}/health` | Health check for a channel |
| `POST` | `/api/config/channels/{channel_name}/restart` | Restart a channel |
| `GET` | `/api/config/channels/{channel}/qrcode` | Get channel authorization QR code |
| `GET` | `/api/config/channels/{channel}/qrcode/status` | Poll channel QR code authorization status |
| `GET` | `/api/config/heartbeat` | Get heartbeat config |
| `PUT` | `/api/config/heartbeat` | Update heartbeat config |
| `POST` | `/api/config/heartbeat/run` | Run heartbeat now |
| `GET` | `/api/config/security/allow-no-auth-hosts` | Get allow no auth hosts configuration |
| `PUT` | `/api/config/security/allow-no-auth-hosts` | Update allow no auth hosts configuration |
| `GET` | `/api/config/security/file-guard` | Get file guard settings |
| `PUT` | `/api/config/security/file-guard` | Update file guard settings |
| `GET` | `/api/config/security/sandbox` | Get global sandbox switch |
| `PUT` | `/api/config/security/sandbox` | Update global sandbox switch |
| `GET` | `/api/config/security/sandbox/deny-paths-protection` | Get deny paths protection status |
| `PUT` | `/api/config/security/sandbox/deny-paths-protection` | Enable or disable deny paths protection |
| `GET` | `/api/config/security/skill-scanner` | Get skill scanner settings |
| `PUT` | `/api/config/security/skill-scanner` | Update skill scanner settings |
| `DELETE` | `/api/config/security/skill-scanner/blocked-history` | Clear all blocked skills history |
| `GET` | `/api/config/security/skill-scanner/blocked-history` | Get blocked skills history |
| `DELETE` | `/api/config/security/skill-scanner/blocked-history/{index}` | Remove a single blocked history entry |
| `POST` | `/api/config/security/skill-scanner/whitelist` | Add a skill to the whitelist |
| `DELETE` | `/api/config/security/skill-scanner/whitelist/{skill_name}` | Remove a skill from the whitelist |
| `GET` | `/api/config/security/tool-guard` | Get tool guard settings |
| `PUT` | `/api/config/security/tool-guard` | Update tool guard settings |
| `GET` | `/api/config/security/tool-guard/builtin-rules` | List built-in guard rules from YAML files |
| `GET` | `/api/config/user-timezone` | Get user timezone |
| `PUT` | `/api/config/user-timezone` | Update user timezone |

## 设置（`settings`，5）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/settings/language` | Get UI language |
| `PUT` | `/api/settings/language` | Update UI language |
| `GET` | `/api/settings/offload-policy` | Get offload default policy |
| `PUT` | `/api/settings/offload-policy` | Update offload default policy |
| `GET` | `/api/settings/upload-limit` | Get upload size limit |

## 备份（`backups`，12）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/backups` | List backups |
| `POST` | `/api/backups/delete` | Delete backups |
| `POST` | `/api/backups/import` | Import backup zip |
| `POST` | `/api/backups/jobs` | Start a backup creation job |
| `GET` | `/api/backups/jobs/active` | Get the active backup creation job |
| `GET` | `/api/backups/jobs/{job_id}` | Get a backup creation job |
| `POST` | `/api/backups/jobs/{job_id}/cancel` | Cancel a backup creation job |
| `GET` | `/api/backups/jobs/{job_id}/events` | Observe a backup creation job via SSE |
| `POST` | `/api/backups/stream` | Create backup with SSE progress stream |
| `GET` | `/api/backups/{backup_id}` | Backup detail |
| `GET` | `/api/backups/{backup_id}/export` | Export backup as zip |
| `POST` | `/api/backups/{backup_id}/restore` | Restore backup |

## Harness（`harnesses`，7）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/harnesses` | Get Harnesses |
| `POST` | `/api/harnesses/{provider_id}/login` | Post Harness Login |
| `POST` | `/api/harnesses/{provider_id}/logout` | Post Harness Logout |
| `GET` | `/api/harnesses/{provider_id}/mcp` | Get Harness Mcp |
| `GET` | `/api/harnesses/{provider_id}/models` | Get Harness Models |
| `GET` | `/api/harnesses/{provider_id}/skills` | Get Harness Skills |
| `POST` | `/api/harnesses/{provider_id}/status` | Post Harness Status |

## Loop（`loops`，8）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/loops` | List Loops |
| `GET` | `/api/loops/custom` | List Custom Modes |
| `POST` | `/api/loops/custom` | Create Custom Mode |
| `DELETE` | `/api/loops/custom/{mode_id}` | Delete Custom Mode |
| `PUT` | `/api/loops/custom/{mode_id}` | Update Custom Mode |
| `POST` | `/api/loops/custom/{mode_id}/duplicate` | Duplicate Custom Mode |
| `GET` | `/api/loops/gates/catalog` | List Gate Catalog |
| `GET` | `/api/loops/status` | Get Loop Status |

## Coding Mode（`coding-mode`，2）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/coding-mode` | Get Coding Mode state for the current agent |
| `POST` | `/api/coding-mode` | Enable or disable Coding Mode for the current agent |

## 插件（`plugins`，8）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/plugins` | List loaded plugins |
| `GET` | `/api/plugins/catalog` | Official plugin catalog |
| `POST` | `/api/plugins/install` | Install plugin from path or URL |
| `GET` | `/api/plugins/market/search` | Search plugins from AgentScope Platform |
| `POST` | `/api/plugins/upload` | Install plugin from ZIP upload |
| `DELETE` | `/api/plugins/{plugin_id}` | Uninstall a plugin |
| `GET` | `/api/plugins/{plugin_id}/files/{file_path}` | Serve plugin static file |
| `GET` | `/api/plugins/{plugin_id}/status` | Get plugin status |

## 前端插件（`frontend-plugin`，2）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/frontend_plugin` | List plugins (public) |
| `GET` | `/api/frontend_plugin/{plugin_id}/files/{file_path}` | Serve plugin static file (public) |

## PawApp（`pawapps`，5）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/pawapps` | List Pawapps |
| `DELETE` | `/api/pawapps/{app_id}` | Uninstall Pawapp |
| `GET` | `/api/pawapps/{app_id}` | Get Pawapp |
| `GET` | `/api/pawapps/{app_id}/settings` | Get Pawapp Settings |
| `GET` | `/api/pawapps/{app_id}/static/{file_path}` | Serve Pawapp Static |

## Market（`market`，3）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/market/categories` | Get Market Categories |
| `GET` | `/api/market/providers` | Get Market Providers |
| `POST` | `/api/market/search` | Market Search |

## 访问控制（`access-control`，13）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/access-control` | Get all access control lists |
| `POST` | `/api/access-control/blacklist/add` | Add one or more users to blacklist |
| `POST` | `/api/access-control/blacklist/remove` | Remove one or more users from blacklist |
| `GET` | `/api/access-control/pending/all` | Get all pending approval entries |
| `POST` | `/api/access-control/pending/approve` | Approve one or more pending users (add to whitelist) |
| `POST` | `/api/access-control/pending/deny` | Deny one or more pending users (add to blacklist) |
| `POST` | `/api/access-control/pending/dismiss` | Dismiss one or more pending users (remove w/o action) |
| `POST` | `/api/access-control/pending/remark` | Update remark on a pending entry |
| `POST` | `/api/access-control/remark` | Update remark for a user in whitelist or blacklist |
| `POST` | `/api/access-control/username` | Update username for a user in any list |
| `POST` | `/api/access-control/whitelist/add` | Add one or more users to whitelist |
| `POST` | `/api/access-control/whitelist/remove` | Remove one or more users from whitelist |
| `GET` | `/api/access-control/{channel}` | Get access control list for a channel |

## 邮件访问控制（`mail-access-control`，13）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/mail-access-control` | Get all mail access control lists |
| `GET` | `/api/mail-access-control/agents` | List all agents with mail access control enabled |
| `POST` | `/api/mail-access-control/blacklist/add` | Add one or more addresses to blacklist |
| `POST` | `/api/mail-access-control/blacklist/remove` | Remove one or more addresses from blacklist |
| `GET` | `/api/mail-access-control/pending/all` | Get all pending approval entries |
| `POST` | `/api/mail-access-control/pending/approve` | Approve one or more pending senders (add to whitelist) |
| `GET` | `/api/mail-access-control/pending/count` | Get pending approval count |
| `POST` | `/api/mail-access-control/pending/deny` | Deny one or more pending senders (add to blacklist) |
| `POST` | `/api/mail-access-control/pending/dismiss` | Dismiss one or more pending senders (remove w/o action) |
| `POST` | `/api/mail-access-control/pending/remark` | Update remark on a pending entry |
| `POST` | `/api/mail-access-control/remark` | Update remark for an address in whitelist or blacklist |
| `POST` | `/api/mail-access-control/whitelist/add` | Add one or more addresses to whitelist |
| `POST` | `/api/mail-access-control/whitelist/remove` | Remove one or more addresses from whitelist |

## 鉴权（`auth`，7）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/auth/login` | Login |
| `POST` | `/api/auth/register` | Register |
| `POST` | `/api/auth/revoke-all-tokens` | Revoke All Sessions |
| `POST` | `/api/auth/revoke-token` | Revoke Single Token |
| `GET` | `/api/auth/status` | Auth Status |
| `POST` | `/api/auth/update-profile` | Update Profile |
| `GET` | `/api/auth/verify` | Verify |

## Fork（`fork`，1）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/fork/agent` | Fork Agent |

## 健康检查（`healthz`，1）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/healthz` | Get Healthz |

## 语音（`voice`，2）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/voice/incoming` | Voice Incoming |
| `POST` | `/voice/status-callback` | Voice Status Callback |

## 浏览器（`browser`，3）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/browser/chrome/self-test` | Self Test |
| `GET` | `/api/browser/chrome/status` | Status |
| `GET` | `/api/browser/chrome/traces` | Traces |

## 其他（11）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | Read Root |
| `POST` | `/api/desktop/shutdown` | Post Desktop Shutdown |
| `GET` | `/api/doctor/runtime` | Get Doctor Runtime |
| `GET` | `/api/version` | Get Version |
| `GET` | `/console` | Console Spa Alias |
| `GET` | `/console/` | Console Spa Alias |
| `GET` | `/console/{full_path}` | Console Spa Alias |
| `GET` | `/crons/jobs` | Auto Cronmanager List Jobs |
| `POST` | `/crons/jobs` | Auto Cronmanager Create Or Replace Job |
| `DELETE` | `/crons/jobs/{job_id}` | Auto Cronmanager Delete Job |
| `GET` | `/{full_path}` | Qwenpaw Console Spa Catchall |

## Agent 作用域镜像

同一个业务接口通常提供两种寻址方式，二者返回结构一致：

- `/api/<资源>` —— 作用于**当前 Agent**（由请求头或默认 Agent 决定）
- `/api/agents/{agentId}/<资源>` —— 作用于**指定 Agent**

共 **209** 个接口提供 `{agentId}` 形式，涵盖：会话（`chats`）、配置与渠道（`config`）、
定时任务（`cron`）、技能（`skills`）、MCP、工具（`tools`）、工作区（`workspace`）、
检查点（`checkpoints`）、Console（`console`）、插件（`plugins`）、导入（`portability`）、
Agent 状态（`agent-status`）等分组。把下表中任一 `/api/...` 路径插入 `/agents/{agentId}` 即为其 Agent 作用域形式。

### 仅提供 Agent 作用域的接口（22）

这些接口没有顶层等价形式，必须带 `{agentId}`：

| 方法 | 完整路径 | 说明 |
| --- | --- | --- |
| `DELETE` | `/api/agents/{agentId}` | Delete agent |
| `GET` | `/api/agents/{agentId}` | Get agent details |
| `PUT` | `/api/agents/{agentId}` | Update agent |
| `GET` | `/api/agents/{agentId}/agent-status` | Get agent status |
| `PATCH` | `/api/agents/{agentId}/backend-settings` | Update third-party backend Chat settings |
| `POST` | `/api/agents/{agentId}/copy` | Copy agent configuration |
| `GET` | `/api/agents/{agentId}/memory/graph` | Get agent memory graph |
| `POST` | `/api/agents/{agentId}/memory/reindex` | Rebuild agent memory index |
| `POST` | `/api/agents/{agentId}/memory/reindex/undo` | Undo a pending embedding index rebuild |
| `GET` | `/api/agents/{agentId}/memory/runtime-status` | Get agent memory runtime state |
| `GET` | `/api/agents/{agentId}/memory/status` | Get agent ReMe memory status |
| `PATCH` | `/api/agents/{agentId}/model-settings` | Update agent model settings |
| `PATCH` | `/api/agents/{agentId}/pin` | Pin or unpin an agent |
| `POST` | `/api/agents/{agentId}/portability/imports/jobs` | Create Import Job |
| `GET` | `/api/agents/{agentId}/portability/imports/jobs/current` | Get Current Import Job |
| `GET` | `/api/agents/{agentId}/portability/imports/jobs/{job_id}` | Get Import Job |
| `POST` | `/api/agents/{agentId}/portability/imports/jobs/{job_id}/cancel` | Cancel Import Job |
| `GET` | `/api/agents/{agentId}/portability/imports/jobs/{job_id}/events` | Stream Import Events |
| `POST` | `/api/agents/{agentId}/portability/imports/jobs/{job_id}/retry` | Retry Import Job |
| `POST` | `/api/agents/{agentId}/portability/imports/jobs/{job_id}/start` | Start Import Job |
| `GET` | `/api/agents/{agentId}/portability/imports/sources` | List Import Sources |
| `PATCH` | `/api/agents/{agentId}/toggle` | Toggle agent enabled state |

## 如何重新生成

```sh
# 1. 启用 OpenAPI 描述并启动后端
QWENPAW_OPENAPI_DOCS=1 uv run python -m qwenpaw app --port 8098

# 2. 拉取描述文件
curl -s http://127.0.0.1:8098/openapi.json -o /tmp/openapi.json
```

默认关闭（`QWENPAW_OPENAPI_DOCS` 未设置时 `/docs`、`/redoc`、`/openapi.json` 均为 404）。
