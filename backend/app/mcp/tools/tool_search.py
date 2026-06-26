"""Platform MCP tool discovery."""
from __future__ import annotations

import logging

from typing import TYPE_CHECKING, Any

from backend.app.mcp.tool_directory import ToolDirectoryService, ToolSearchQuery
from backend.app.mcp.tools.base import BaseTool

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext

logger = logging.getLogger(__name__)


def _searchable_app_domains() -> list[tuple[str, str]]:
    """从已注册 AI-Native 应用 manifest 的 `domain_summary` 汇聚「可搜索域目录」。

    每个 manifest 的 `domain_summary` 是 ``{namespace 关键词: 一句话}``（如
    ``{'deck': '演示文稿（…）'}``；`hasn_task` 同时含 ``task``/``workflow`` 两域）。
    **数据驱动**：新增 AI-Native 应用只要在其 manifest 声明 `domain_summary`，本目录与
    `tool.search` 描述即自动多一行——无需手改本文件（零硬编码，自动注册）。
    去重保序（按 namespace 字母序），缺声明的应用诚实跳过、不臆造。
    """
    from backend.app.hasn_core.app_platform import ai_native_app_registry

    pairs: dict[str, str] = {}
    for manifest in ai_native_app_registry.list_builtin_apps():
        summary = manifest.get('domain_summary')
        if not isinstance(summary, dict):
            continue
        for namespace, text in summary.items():
            ns = str(namespace).strip()
            label = str(text).strip()
            if ns and label and ns not in pairs:
                pairs[ns] = label
    return sorted(pairs.items())


def _build_search_description() -> str:
    """固定用法说明 + 数据驱动的「可搜索的应用域」目录（按 manifest domain_summary 汇聚）。"""
    base = (
        '发现并取用当前 Agent 可用的云端 MCP 工具（来源/摘要/schema）。query 用法：\n'
        '- "sources"：列出工具来源分类（platform/app 计数）\n'
        '- "platform" / "apps" / "app.<域>"（如 "app.community"）：按来源/应用域列工具\n'
        '- "<关键词>"（如 "community"、"演示文稿"、"回测"）：按工具名/描述/域模糊搜索\n'
        '- "tool:<工具名>"（如 "tool:hasn.deck.create"）：取该工具完整 schema'
    )
    try:
        domains = _searchable_app_domains()
    except Exception:  # 描述仅作引导，汇聚失败不应炸 tools/list；诚实回落基础说明，不臆造域。
        logger.warning('build searchable app domain catalog failed', exc_info=True)
        return base
    if not domains:
        return base
    lines = '\n'.join(f'- {ns}：{label}' for ns, label in domains)
    return f'{base}\n可搜索的应用域（用关键词搜该域工具）：\n{lines}'


class ToolSearchTool(BaseTool):
    """Search visible MCP tool sources, summaries, and schemas."""

    @property
    def source(self) -> str:
        return "platform"

    def __init__(self, directory: ToolDirectoryService) -> None:
        self._directory = directory

    @property
    def name(self) -> str:
        return "hasn.cloud.tool.search"

    @property
    def description(self) -> str:
        # 动态描述：每新增一个 AI-Native 应用（manifest 声明 domain_summary）即自动多一行域目录。
        return _build_search_description()

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "sources、platform、apps、app.crm、tool:hasn.crm.lead.create、crm lead create",
                },
                "source": {
                    "type": "string",
                    "enum": ["all", "platform", "app", "external", "local"],
                    "default": "all",
                },
                "detail": {
                    "type": "string",
                    "enum": ["sources", "summary", "schema"],
                    "default": "summary",
                },
                "page_size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 20,
                },
                "cursor": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        }

    async def execute(
        self,
        agent_context: AgentContext,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        query = ToolSearchQuery(
            query=str(arguments["query"]),
            source=str(arguments.get("source", "all")),
            detail=str(arguments.get("detail", "summary")),
            page_size=int(arguments.get("page_size", 20)),
            cursor=arguments.get("cursor"),
        )
        return await self._directory.search(agent_context, query)
