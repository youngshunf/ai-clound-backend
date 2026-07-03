"""G3 应用权益门 namespace→app_id 注册表守卫（doc18 §4.3 · 实施/103 U3）。

守卫本体零 mock，只对**真实注册表**求值：
1. 每个已注册的非 external 平台工具，其 namespace 必在 NAMESPACE_TO_APP_ID 显式登记
   （None 也须显式写）——漏登记 = 测试红，防新应用工具静默跳过权益门；
2. GATED_APP_IDS = 去 None 去重的商业化 app_id 集合，非空且不含 None；
3. resolve_tool_app_id 优先取工具自带 app_id（AI-Native app 工具覆盖 namespace 回填）。
"""

from __future__ import annotations

from typing import Any

from backend.app.mcp.server import HasnCloudMcpServer
from backend.app.mcp.tool_app_registry import (
    GATED_APP_IDS,
    NAMESPACE_TO_APP_ID,
    resolve_tool_app_id,
)
from backend.app.mcp.tools.base import BaseTool


def test_every_registered_platform_namespace_is_declared() -> None:
    """真实注册表里每个非 external 工具的 namespace 必在 NAMESPACE_TO_APP_ID 显式登记。"""
    server = HasnCloudMcpServer()
    missing: set[str] = set()
    for tool in server.tool_registry.get_all_tools():
        if getattr(tool, 'source', 'platform') == 'external':
            continue
        namespace = tool.namespace
        if namespace not in NAMESPACE_TO_APP_ID:
            missing.add(namespace)
    assert not missing, f'这些平台工具 namespace 未在 tool_app_registry 登记（漏挂 G3）：{sorted(missing)}'


def test_gated_app_ids_derived_and_nonempty() -> None:
    """GATED_APP_IDS = 去 None 去重；非空（有商业化应用挂门）且绝不含 None。"""
    assert GATED_APP_IDS  # 至少 deck/task/plan/designsystem 之一
    assert None not in GATED_APP_IDS
    assert frozenset(v for v in NAMESPACE_TO_APP_ID.values() if v) == GATED_APP_IDS
    # task 应用的 catalog app_id 是 hasn_task（非 task）；workflow 同属 task 应用
    assert NAMESPACE_TO_APP_ID['hasn.task'] == 'hasn_task'
    assert NAMESPACE_TO_APP_ID['hasn.workflow'] == 'hasn_task'


def test_base_tool_namespaces_map_to_none() -> None:
    """底座能力（消息/会话/资产/记忆/通知/工具发现…）一律 None，跳过 G3。"""
    for ns in ('hasn.message', 'hasn.conversation', 'hasn.asset', 'hasn.memory',
               'hasn.notifications', 'hasn.cloud', 'hasn.contact', 'hasn.user',
               'hasn.owner', 'hasn.artifact', 'hasn.marketplace', 'hasn.workbench'):
        assert NAMESPACE_TO_APP_ID.get(ns) is None, f'{ns} 应为底座（None），不该挂 G3'


class _ExplicitAppTool(BaseTool):
    """AI-Native app 工具 stub：自带 app_id 覆盖 namespace 回填。"""

    @property
    def source(self) -> str:
        return 'app'

    @property
    def name(self) -> str:
        return 'hasn.someapp.do'

    @property
    def app_id(self) -> str | None:
        return 'someapp'

    @property
    def description(self) -> str:
        return 'app tool'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    async def execute(self, agent_context: object, arguments: dict[str, Any]) -> dict[str, Any]:
        return {}


def test_resolve_prefers_explicit_app_id() -> None:
    """工具自带 app_id 优先于 namespace 回填（AI-Native app 工具）。"""
    assert resolve_tool_app_id(_ExplicitAppTool()) == 'someapp'


def test_resolve_falls_back_to_namespace() -> None:
    """无自带 app_id 的平台工具按 namespace 回填。"""

    class _DeckTool(_ExplicitAppTool):
        @property
        def source(self) -> str:
            return 'platform'

        @property
        def name(self) -> str:
            return 'hasn.deck.create'

        @property
        def app_id(self) -> str | None:
            return None

    assert resolve_tool_app_id(_DeckTool()) == 'deck'
