// Extracted verbatim from the original main.ts inline template.
// Element ids and classes are selected on by the Vitest and Playwright suites.

export const managementView = `
        <!-- View 5: Manage -->
        <section id="manage" class="tab" hidden>
          <div class="workspace-card gap-10">
            <div class="row">
              <button id="load-models" class="primary">读取模型配置</button>
              <button id="load-channels" class="secondary">读取渠道列表</button>
              <button id="load-cron" class="secondary">读取定时任务</button>
            </div>
            <pre id="management-result" class="result">尚未读取配置</pre>
          </div>
          <div class="forms-grid">
            <form id="model-form" class="subcard">
              <h3>设置 Agent 模型</h3>
              <label>Provider ID<input id="provider-id" value="tlprovider" required placeholder="例如 tlprovider 或 tlproxy"></label>
              <label>Model ID<input id="model-id" value="deepseek-v4-flash" required placeholder="例如 deepseek-v4-flash"></label>
              <button class="primary">保存模型选择</button>
            </form>
            <form id="provider-form" class="subcard">
              <h3>Provider 连接配置</h3>
              <label>Provider ID<input id="config-provider-id" value="tlprovider" required placeholder="例如 tlprovider 或 tlproxy"></label>
              <label>Base URL<input id="provider-url" type="url" value="http://127.0.0.1:8089" required></label>
              <label>API Key<input id="provider-key" type="password" autocomplete="off" placeholder="留空时不提交此字段"></label>
              <button class="primary">保存 Provider 配置</button>
            </form>
          </div>
          <div id="service-tl-panel" class="subcard" hidden>
            <h3>平台 TL Provider · 多用户服务</h3>
            <p id="service-tl-model">连接服务后读取平台模型与配置版本。</p>
            <p>服务聊天使用平台 AssistantDefinition 中的 model_protocol、base_url 和 tl_config。修改平台配置时请更新 version，新任务使用新版本，已有任务继续使用原配置。</p>
            <p>本地入口：npm run service:tl。配置文件：deploy/server/assistant.tl.local.json；生产使用 assistant.tl.json。TL 鉴权密钥由 QWENPAW_SERVER_MODEL_API_KEY 提供。</p>
            <p>TL 实际上游模型由代理配置决定；个人 Agent 的“用于当前 Agent”设置不影响这里。</p>
          </div>
          <div id="tl-panel"></div>
          <form id="channel-form" class="subcard">
            <h3>渠道配置</h3>
            <div class="row">
              <label>渠道<select id="channel-name"><option value="console">console</option></select></label>
              <button id="read-channel" type="button" class="secondary">读取该渠道</button>
            </div>
            <label>配置 JSON<textarea id="channel-json" rows="7" spellcheck="false" placeholder="先读取渠道配置，再编辑"></textarea></label>
            <button class="primary">保存渠道配置</button>
          </form>
          <div class="subcard">
            <h3>定时任务</h3>
            <div id="cron-list" class="stack-gap-6"></div>
            <form id="cron-form" class="stack-gap-8-top-8">
              <div class="row">
                <label>任务名称<input id="cron-name" required></label>
                <label>Cron 表达式<input id="cron-expression" value="0 9 * * *" required></label>
                <label>时区<input id="cron-timezone" value="Asia/Shanghai" required></label>
              </div>
              <label>通知文本<input id="cron-text" required></label>
              <button class="primary">创建（默认暂停）</button>
            </form>
          </div>
        </section>
`;
