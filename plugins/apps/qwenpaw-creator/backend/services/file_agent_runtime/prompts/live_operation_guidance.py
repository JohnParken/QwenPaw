# -*- coding: utf-8 -*-
"""Teach live operation the way the host does: reuse its own browser skill.

The authoritative API surface belongs to the main repository, so it is loaded
from the installed browser skill verbatim rather than restated here — a copy
would drift, and a drifted manual teaches a closed API that no longer exists.
Creator only appends what is genuinely its own: how recording works, and where
the resulting footage ends up.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILL_CANDIDATES = ("browser-zh", "browser-en")
_FRONT_MATTER_FENCE = "---"

# Creator's own contract on top of the host SDK. Recording bounds are the only
# thing the model must decide deliberately, so that is what this states — with
# no prescribed order of work, because the flow is the model's to design.
_CREATOR_SECTION = """
## Creator 扩展：录制与产物（`recorder`）

`browser_use` 的作用域里除 `Browser` 外还有 `recorder`：

**每一次 `browser_use` 调用都是新的隔离会话，变量绝不跨调用保留。** 每段 code
都必须先执行 `browser = await Browser.connect()`，再 open/present 目标页面；即使
上一条工具结果里出现过 `browser` / `page`，也不得直接沿用。一次调用中可以完成
观察，也可以完成录制；如果先观察、下一次才录制，第二次仍须重新 connect/open。
代码直接使用顶层 `await`、`if`、`try` 和有界 `for`；不得定义 `def` / `async def`、
`lambda` 或 `class`，这些定义会被运行时拒绝，以免递归或自建控制流阻塞同进程任务。
桥接只开放教程操作所需的 Browser/Page API；会话生命周期由运行时负责，
`browser.close()` 不可用，也不应由模型主动调用。open/present/goto 只接受绝对
HTTP(S) URL；`file:`、`data:`、`javascript:` 等本地或脚本 scheme 会被拒绝。

- `await recorder.start(label="这段在做什么") -> take_id`：开始录制当前操作的页面。
- `await recorder.stop() -> {take_id, label, summary}`：结束这一段并落地成片段。
- `recorder.is_recording() -> bool`：当前是否正在录制。

只有 `start` 与 `stop` 之间的画面会进入录像。感知、思考、试错、等待都发生在录制
区间之外，因此片段里不会有废镜头——这也是让后续读片和剪辑成本保持低的原因。

**是否需要录屏由你判断**：真实的动态过程（连续操作、页面跳转、滚动浏览）值得录；
只是要展示某个静态界面时，`await page.screenshot()` 配合动效通常更省也更清楚。
需要重录时，重新操作一遍再录即可。

若用户明确要“操作过程录像 / 软件教程镜头 / 动态演示素材”，验收时必须至少返回
一个可播放 take；只有观察或 print、工具结果中的 `takes` 仍为空，都不算完成。可以先
在一次调用中观察真实界面，再根据工具结果发起下一次调用完成录制；开始录制后必须
做至少一个真实可见动作，并在复核结果后 stop。
无媒体的合法观察调用会明确返回 `observationOnly=true`、
`completionEligible=false`；可以使用其输出继续工作，但不可据此结束教程任务。

“真实可见动作”必须是实际 await 的页面变更操作，例如 locator.click/scroll、
page.goto/go_back/reload 或真实输入；wait_for_timeout、snapshot、print 只是等待/感知，
不是动作。take 的 summary 若显示 `0 actions` 会被质量门拒绝，必须重新录制，不能把
注释里写着“滚动”但实际只 wait 的代码当成操作。

产物会自动成为 Project 源素材，与用户上传的素材完全同构：
- 录像片段 → 源素材（可用 `observe_source_clip` 看片、可作为 Edit Element 的
  `render_source` 进入时间轴）；
- 截图 → 项目图片素材（可上轨、可作参考图）；
- 工具返回值里给出每个片段与截图的 `workspaceRef` / `sourceAssetVersionId`，
  后续委派与编排直接引用这些 ID。

录制期间，每个操作的坐标与时刻会被自动记录在该片段的事实清单里（你无需做任何
额外的事），动效制作会用它把强调放在操作真正发生的位置上。

用户要求的是“教程成片”而不只是原始录屏时，素材够用后必须把目标 Timeline 与
“按事实清单做总览→聚焦→结果证明；目标不可被文字遮挡；统一教程字幕；完成后逐场景
复核”的任务委派给 AI 剪辑导演。除非用户只要机械裁切，不要由主 Agent 把长 take
直接铺满时间线再叠加大号说明框；剪辑导演会收到 Runtime 注入的动作账本，并负责
用真实动作时刻拆镜、聚焦、包装和验收。

**素材达到验收标准后立即停止采集。** 已有可播放 take、截图和事实清单足以覆盖用户
要求时，不要为了“更完整”继续录屏、截图或重复 grounding。复用工具返回的
`sourceAssetVersionId` 与事实清单；编排阶段先一次性查询所需 Project 状态，再用少量
批量 `patch_project` 写入时间线，不要逐素材重读、逐元素往返。定位器试错只用于让
真实操作成功，不能演变成无上限的素材研究。

### 本后端已验证的注意事项

- `await locator.scroll()` 的语义是“把这个 locator 滚入视野”，不是持续滚动当前
  页面；对已经可见的 `body` 调用可能记录了 action 但画面完全不动。教程里要展示页面
  连续下滚时，用 `await page.keyboard.press("PageDown")`（回顶用 `Home`），每次动作后
  observe/screenshot 确认页面内容真的变化；坐标滚轮 `page.mouse.wheel(...)` 在当前后端
  不受支持，会直接失败。
- 定位失败时先 `await page.snapshot()` 看真实的 role 与可访问名，不要凭猜测重试
  同一个定位器。
- 登录、验证码、2FA 一律 `await browser.handoff(...)` 后停止，绝不自动化。
"""


def _skill_root() -> Path | None:
    try:
        import qwenpaw
    except ImportError:  # pragma: no cover - Creator always runs inside host
        return None
    module_file = getattr(qwenpaw, "__file__", None)
    if not module_file:
        return None
    return Path(module_file).resolve().parent / "agents" / "skills"


def _strip_front_matter(text: str) -> str:
    """Drop the skill's YAML header, which is loader metadata, not guidance."""
    stripped = text.lstrip()
    if not stripped.startswith(_FRONT_MATTER_FENCE):
        return text.strip()
    closing = stripped.find(
        f"\n{_FRONT_MATTER_FENCE}",
        len(_FRONT_MATTER_FENCE),
    )
    if closing == -1:
        return stripped
    remainder = stripped[closing + len(_FRONT_MATTER_FENCE) + 1 :]
    return remainder.strip()


def load_host_browser_manual() -> str:
    """Return the host's browser skill body, or an empty string if absent."""
    root = _skill_root()
    if root is None:
        return ""
    for name in _SKILL_CANDIDATES:
        candidate = root / name / "SKILL.md"
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        body = _strip_front_matter(text)
        if body:
            return body
    logger.info(
        "host browser skill unavailable; live guidance is minimal",
    )
    return ""


def live_operation_guidance() -> str:
    """Render guidance for exactly the enabled live-operation tools."""
    from models.config import get_live_operation_enabled

    parts: list[str] = []
    if get_live_operation_enabled():
        manual = load_host_browser_manual()
        parts.append(
            "# 真实网站操作（`browser_use`）\n\n"
            "`browser_use` 让你用异步 Python 真实操作网站：驱动的是 "
            "QwenPaw 自己的 "
            "Browser SDK，不是 Playwright，未列出的方法不存在。下面是该 SDK 的完整"
            "参考，以及 Creator 侧的录制约定。流程怎么组织由你决定。",
        )
        if manual:
            parts.append(manual)
        parts.append(_CREATOR_SECTION.strip())
    return "\n\n".join(part for part in parts if part)


__all__ = [
    "live_operation_guidance",
    "load_host_browser_manual",
]
