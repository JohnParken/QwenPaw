import { ApiClient } from "./api";
import { escapeHtml } from "./markdown";
import type { RecordValue } from "./chat";

export class SkillsPanel {
  private container: HTMLElement;

  constructor(
    private api: ApiClient,
    private run: (task: () => Promise<void>, success?: string) => Promise<void>,
    containerId = "skills-panel-root",
  ) {
    let el = document.getElementById(containerId);
    if (!el) {
      el = document.createElement("div");
      el.id = containerId;
    }
    this.container = el;
    this.render();
  }

  get element(): HTMLElement {
    return this.container;
  }

  private render(): void {
    this.container.innerHTML = `
      <div class="skills-panel-container">
        <div class="skills-toolbar">
          <div>
            <h2>🧩 智能体技能与扩展能力 (Skills Management)</h2>
            <p class="hint">管理当前 Agent 加载的技能扩展，支持热开启/关闭以测试 Agent 决策影响。</p>
          </div>
          <div class="row">
            <button type="button" id="skills-refresh-btn" class="primary">刷新技能列表</button>
          </div>
        </div>

        <div id="skills-list-grid" class="skills-grid">
          <p class="muted-hint">点击「刷新技能列表」加载当前 Agent 技能</p>
        </div>
      </div>
    `;

    this.container
      .querySelector("#skills-refresh-btn")
      ?.addEventListener("click", () => {
        void this.run(() => this.loadSkills(), "技能列表已刷新");
      });
  }

  async loadSkills(): Promise<void> {
    const gridEl =
      this.container.querySelector<HTMLElement>("#skills-list-grid");
    if (!gridEl) return;

    try {
      const skills = await this.api.request<RecordValue[]>("/api/skills");
      if (!Array.isArray(skills) || skills.length === 0) {
        gridEl.innerHTML = '<p class="muted-hint">当前 Agent 暂无可用技能</p>';
        return;
      }

      gridEl.innerHTML = "";
      for (const skill of skills) {
        const card = document.createElement("div");
        const isEnabled = skill.enabled !== false;
        card.className = `skill-card ${isEnabled ? "enabled" : "disabled"}`;
        card.innerHTML = `
          <div class="skill-card-header">
            <span class="skill-card-title">🧩 ${escapeHtml(skill.name || skill.id || "技能")}</span>
            <span class="skill-status-pill ${isEnabled ? "on" : "off"}">${isEnabled ? "已启用" : "已禁用"}</span>
          </div>
          <div class="skill-card-desc">${escapeHtml(skill.description || "暂无描述")}</div>
          <div class="skill-card-meta">
            ${skill.version ? `<span class="skill-meta-tag">v${escapeHtml(skill.version)}</span>` : ""}
            ${skill.author ? `<span class="skill-meta-tag">${escapeHtml(skill.author)}</span>` : ""}
          </div>
          <div class="skill-card-footer">
            <button type="button" class="btn-toggle-skill ${isEnabled ? "secondary" : "primary"}">
              ${isEnabled ? "禁用技能" : "启用技能"}
            </button>
          </div>
        `;

        card
          .querySelector(".btn-toggle-skill")
          ?.addEventListener("click", () => {
            const action = isEnabled ? "disable" : "enable";
            void this.run(
              async () => {
                await this.api.request(
                  `/api/skills/${encodeURIComponent(skill.name || skill.id)}/${action}`,
                  {
                    method: "POST",
                  },
                );
                await this.loadSkills();
              },
              `技能 ${skill.name} 已${isEnabled ? "禁用" : "启用"}`,
            );
          });

        gridEl.appendChild(card);
      }
    } catch {
      gridEl.innerHTML =
        '<p class="muted-hint">无法获取技能列表或后端未启用技能系统</p>';
    }
  }
}
