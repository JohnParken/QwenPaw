import type { TokenUsage, ContextUsage } from "./chat";

export class ContextMonitor {
  private container: HTMLElement;

  constructor(containerId = "context-monitor-root") {
    let el = document.getElementById(containerId);
    if (!el) {
      el = document.createElement("div");
      el.id = containerId;
      el.className = "context-monitor-bar";
    }
    this.container = el;
    this.renderEmpty();
  }

  get element(): HTMLElement {
    return this.container;
  }

  update(turn: TokenUsage | null, ctx: ContextUsage | null): void {
    if (!turn && !ctx) {
      this.renderEmpty();
      return;
    }

    const est = ctx?.estimated_tokens ?? 0;
    const max = ctx?.max_input_length ?? 0;
    const ratio = ctx?.context_usage_ratio
      ? Math.round(ctx.context_usage_ratio * 10) / 10
      : max > 0
        ? Math.round((est / max) * 1000) / 10
        : 0;

    const ratioCls = ratio > 85 ? "danger" : ratio > 65 ? "warning" : "normal";

    const promptT = turn?.prompt_tokens ?? "-";
    const compT = turn?.completion_tokens ?? "-";
    const totalT = turn?.total_tokens ?? "-";

    this.container.innerHTML = `
      <div class="ctx-meter-wrapper" title="当前会话上下文水位与 Token 消耗">
        <div class="ctx-stats-row">
          <span class="ctx-title">Context 窗口:</span>
          <span class="ctx-ratio-pill ${ratioCls}">${ratio}%</span>
          <span class="ctx-nums">${est ? est.toLocaleString() : "-"} / ${max ? max.toLocaleString() : "-"}</span>
          <span class="ctx-sep">|</span>
          <span class="ctx-tokens">本轮 Token: 入 ${promptT} · 出 ${compT} · 计 ${totalT}</span>
        </div>
        <div class="ctx-progress-track">
          <div class="ctx-progress-bar ${ratioCls}" style="width: ${Math.min(100, Math.max(0, ratio))}%"></div>
        </div>
      </div>
    `;
  }

  private renderEmpty(): void {
    this.container.innerHTML = `
      <div class="ctx-meter-wrapper empty">
        <span class="ctx-title">Context: 就绪</span>
        <span class="ctx-nums">等待对话生成以计算窗口消耗</span>
      </div>
    `;
  }
}
