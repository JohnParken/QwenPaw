// Extracted verbatim from the original main.ts inline template.
// Element ids and classes are selected on by the Vitest and Playwright suites.

export const filesView = `
        <!-- View 2: Files -->
        <section id="files" class="tab" hidden>
          <div class="workspace-card">
            <div class="workspace-toolbar">
              <label class="field-label-strong">文件根目录:
                <select id="file-root" class="select-root">
                  <option value="workspace">Agent workspace</option>
                  <option value="project">当前会话 project</option>
                </select>
              </label>
              <form id="browse-form" class="row row-margin-0">
                <label class="field-label">目录路径:
                  <input id="directory" placeholder="空值为根目录" class="input-dir">
                </label>
                <button class="primary">浏览目录</button>
                <button type="button" id="more-files" disabled class="secondary">下一页</button>
              </form>
            </div>
            <div id="file-list" class="file-list"></div>
          </div>

          <div class="workspace-card">
            <form id="open-form" class="row row-margin-0">
              <label class="field-label-grow">文件路径:
                <input id="file-path" placeholder="从列表选择或输入相对路径" required class="input-grow">
              </label>
              <button class="primary">读取文件</button>
            </form>
            <p id="file-version" class="muted">未读取文件；保存使用 ETag 防止覆盖其他修改。</p>
            <div class="file-editor-area">
              <label class="field-label-text">文本内容
                <textarea id="file-content" rows="12" spellcheck="false"></textarea>
              </label>
              <div class="row">
                <button id="save-file" class="primary">保存文件</button>
                <button id="download-file" class="secondary">下载文件</button>
              </div>
            </div>
          </div>

          <form id="upload-form" class="workspace-card">
            <h3 class="section-title">上传文件到当前目录</h3>
            <input id="upload-files" type="file" multiple required>
            <p class="hint row-margin-0">同名文件采用 rename 策略。文本编辑上限 2 MiB；二进制文件使用下载。</p>
            <button class="primary align-start">上传文件</button>
          </form>

          <div id="checkpoints-mount"></div>
        </section>
`;
