"""工具 namespace → catalog app_id 映射（doc18 G3 应用权益门 · 实施/103 U3）。

平台工具（source=platform）本身不带 app_id，其「所属应用」由 namespace 在此表**显式登记**
回填；AI-Native app 工具（source=app）天然带 manifest app_id、覆盖本表。
`app_id=None` = 平台底座（工具发现/资产/消息/会话/通知/记忆/运维…）**跳过 G3 权益门**。

守卫（test_g3_app_registry）：每个已注册的非 external 工具 namespace 必须在此显式登记
（None 也须显式写）；漏登记 = 测试红，防新应用工具静默跳过权益门。

app_id 值对照 `hasn_app_catalog`（注意 task 应用的 catalog app_id 是 `hasn_task`，
workflow 工具同属 task 应用）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.mcp.tools.base import BaseTool

# namespace → catalog app_id（None = 平台底座，跳过 G3）。
# 商业化应用工具（deck/task+workflow/plan/designsystem…）映射到其 catalog app_id；
# 底座能力（工具发现/资产/消息/会话/通知/记忆/主人画像/市场/工作台简报…）一律 None。
NAMESPACE_TO_APP_ID: dict[str, str | None] = {
    # ── 平台底座（app_id=None·G3 跳过；G1/G5 仍管）──
    'hasn.artifact': None,  # 产物记录（底座）
    'hasn.asset': None,  # 资产创建（底座）
    'hasn.cloud': None,  # 工具发现 / 转发元工具（底座）
    'hasn.contact': None,  # 通讯录（社交底座）
    'hasn.conversation': None,  # 会话列表（消息底座）
    'hasn.marketplace': None,  # 能力市场本身（底座，非被购应用）
    'hasn.memory': None,  # 记忆（每个分身自带的底座能力）
    'hasn.message': None,  # 消息收发（底座）
    'hasn.notifications': None,  # 通知（底座）
    'hasn.owner': None,  # 主人画像/记忆采访（KNOWU 底座）
    'hasn.user': None,  # 用户搜索（底座）
    'hasn.workbench': None,  # 工作台简报（home 底座，非被购应用）
    # ── 商业化应用（app_id 对照 hasn_app_catalog）──
    'hasn.deck': 'deck',
    'hasn.designsystem': 'designsystem',
    'hasn.plan': 'plan',
    'hasn.task': 'hasn_task',
    'hasn.workflow': 'hasn_task',  # 工作流工具同属 task 应用
}

# G3 挂门的 catalog app_id 集合（去 None 去重）——per-request 预取只需这几个（103 §4 性能条）。
GATED_APP_IDS: frozenset[str] = frozenset(v for v in NAMESPACE_TO_APP_ID.values() if v)


def resolve_tool_app_id(tool: BaseTool) -> str | None:
    """解析工具的有效 app_id（G3 权益门用）。

    优先取工具自带 app_id（AI-Native app 工具 manifest 声明）；平台工具无自带值时，
    按 namespace 从 `NAMESPACE_TO_APP_ID` 回填。未登记 namespace → None（不挂门，宽松兜底；
    守卫测试确保平台工具 namespace 不会漏登记）。
    """
    explicit = getattr(tool, 'app_id', None)
    if explicit:
        return explicit
    namespace = getattr(tool, 'namespace', None) or _fallback_namespace(getattr(tool, 'name', ''))
    return NAMESPACE_TO_APP_ID.get(namespace)


def _fallback_namespace(tool_name: str) -> str:
    """无 namespace 属性的兜底，口径与 BaseTool.namespace 默认一致。"""
    parts = tool_name.split('.')
    if len(parts) < 2:
        return tool_name
    if tool_name.startswith('hasn.ext.') and len(parts) >= 3:
        return '.'.join(parts[:3])
    return '.'.join(parts[:2])
