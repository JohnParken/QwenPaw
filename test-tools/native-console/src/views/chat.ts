// Extracted verbatim from the original main.ts inline template.
// Element ids and classes are selected on by the Vitest and Playwright suites.

export const chatView = `
        <!-- View 1: Chat -->
        <section id="chat" class="tab">
          <div id="messages" class="chat-messages-scroll" role="log" aria-label="聊天消息"></div>

          <!-- Quick Test Presets Bar -->
          <div class="chat-presets-bar">
            <span class="presets-label">⚡ 快捷能力测试:</span>
            <button type="button" class="preset-chip" data-prompt="你好，请介绍一下 QwenPaw Agent 的主要功能和你能使用的工具。">基础问答</button>
            <button type="button" class="preset-chip" data-prompt="请详细推演并解答经典的农夫、狼、羊、白菜过河问题，给出每一步的状态变换和思考逻辑。">深度推理 (R1)</button>
            <button type="button" class="preset-chip" data-prompt="请在当前工作区创建一个 test-note.txt 文件并写入当前时间，然后再读取它。">文件读写工具</button>
            <button type="button" class="preset-chip" data-prompt="请执行终端命令查看当前目录下的文件列表和 Node 版本。">Shell 工具</button>
            <button type="button" class="preset-chip" data-prompt="请执行终端高危命令 cat /etc/hosts，测试工具安全审批拦截。">触发安全审批</button>
          </div>

          <!-- Chat Input Bar -->
          <form id="send-form" class="chat-input-bar">
            <div class="chat-input-wrapper">
              <textarea id="prompt" rows="2" placeholder="输入给 QwenPaw Agent 的指令... (Enter 发送，Shift+Enter 换行)" required></textarea>
              <div class="input-controls-row">
                <div class="input-controls-left">
                  <label class="file-upload-btn-label">📎 上传附件<input id="chat-file" type="file"></label>
                  <span id="chat-file-badge" class="attached-filename"></span>
                  <button type="button" id="history" class="header-btn btn-compact-11">加载历史</button>
                </div>
                <div class="input-controls-right">
                  <button id="send" class="btn-send">发送 ↵</button>
                </div>
              </div>
            </div>
          </form>
        </section>
`;
