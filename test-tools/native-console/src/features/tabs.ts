// Sidebar tab navigation. Each tab lazily refreshes the panel it reveals.

import type { CheckpointsPanel } from "../checkpoints";
import type { InboxPanel } from "../inbox";
import type { SkillsPanel } from "../skills";

export function bindTabs(deps: {
  inboxPanel: InboxPanel;
  skillsPanel: SkillsPanel;
  checkpointsPanel: CheckpointsPanel;
}): void {
  const { inboxPanel, skillsPanel, checkpointsPanel } = deps;

  document.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach((button) =>
    button.addEventListener("click", () => {
      document.querySelectorAll<HTMLElement>(".tab").forEach((tab) => {
        tab.hidden = tab.id !== button.dataset.tab;
      });
      document
        .querySelectorAll("[data-tab]")
        .forEach((item) => item.removeAttribute("aria-current"));
      button.setAttribute("aria-current", "page");
      if (button.dataset.tab === "inbox") void inboxPanel.loadAll();
      if (button.dataset.tab === "skills") void skillsPanel.loadSkills();
      if (button.dataset.tab === "files")
        void checkpointsPanel.loadCheckpoints();
    }),
  );
}
