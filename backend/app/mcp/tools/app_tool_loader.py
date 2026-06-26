"""把 AI-Native App manifest 的 capability 投影成 MCP App 工具（P4-B，Q1）。

闭合 GAP：云端 `load_app_tools_*` 原直接 return []，导致 App manifest 的 capability
从未进入 `tool.search` / catalog。本模块按已发布 manifest（builtin + DB published）
把每个 capability 包成 `AppTool`（source='app'，dispatch 走 ai_native_runtime_gateway）。

零 fake：无 manifest / 无 capability → 返回空 list，不造假。
"""

from __future__ import annotations

import logging

from typing import Any

from backend.app.mcp.tools.app_tools import AppTool

logger = logging.getLogger(__name__)


def _split_mcp_name(mcp_name: str) -> tuple[str, str] | None:
    """'hasn.community.get_feed' → (app_namespace='community', action='get_feed')。

    非 hasn.<ns>.<action...> 形态返回 None（跳过，不造假）。
    """
    parts = mcp_name.split('.')
    if len(parts) < 3 or parts[0] != 'hasn':
        return None
    return parts[1], '.'.join(parts[2:])


def build_app_tools_from_manifest(manifest_payload: dict[str, Any]) -> list[AppTool]:
    """从单个 manifest payload 的 capabilities 构造 AppTool 列表。

    **仅投影云端可调度的 capability**：一个 capability 是云端工具，当且仅当其
    ``tool_id`` 出现在 manifest 的 ``tools[]``（云端 Runtime Gateway 的 handler 清单，
    与 ``ai_native_runtime_gateway._find_tool`` 调度权威一致）。本地数据面应用
    （task/deck/publish/film/reel 等，``tools[]`` 置空、capability 仅承载发现/权限
    控制面元数据）的 capability **不**投影成云端工具——否则 `tool.search` 会把它们
    宣称为 `execution_location=cloud` 可调，调用却因无云端 handler 报「AI-Native 工具
    不存在」（发现/调度契约漂移）。这类工具的数据面在本地 hasn-mcp，由本地 MCP 面发现调用。
    零 fake：``tools[]`` 空 → 不投影任何云端工具。
    """
    manifest_json = manifest_payload.get('manifest_json') or {}
    app_id = manifest_json.get('app_id') or manifest_payload.get('app_id')
    if not app_id:
        return []

    cloud_tool_ids = {
        t.get('tool_id') for t in (manifest_json.get('tools') or []) if t.get('tool_id')
    }

    installation_id = f'manifest:{manifest_payload.get("id") or app_id}'
    tools: list[AppTool] = []
    for cap in manifest_json.get('capabilities', []):
        mcp_name = cap.get('mcp_name')
        tool_id = cap.get('tool_id')
        if not mcp_name or not tool_id:
            continue
        # 调度权威：无对应云端 handler（tool_id 不在 tools[]）的 capability 不进云端发现面。
        if tool_id not in cloud_tool_ids:
            continue
        split = _split_mcp_name(str(mcp_name))
        if split is None:
            continue
        app_namespace, action = split
        try:
            tools.append(
                AppTool(
                    installation_id=installation_id,
                    app_id=str(app_id),
                    app_namespace=app_namespace,
                    tool_id=str(tool_id),
                    tool_name=action,
                    action=action,
                    tool_description=str(cap.get('description') or cap.get('name') or ''),
                    tool_input_schema=cap.get('input_schema') or {'type': 'object'},
                    tool_required_scopes=list(cap.get('required_scopes') or []),
                    tool_output_schema=cap.get('output_schema'),
                    risk_level=str(cap.get('risk_level') or 'low'),
                    execution_location='cloud',
                )
            )
        except Exception:
            logger.warning('skip invalid app capability mcp_name=%s app_id=%s', mcp_name, app_id, exc_info=True)
    return tools


async def load_published_app_tools() -> list[AppTool]:
    """加载所有 workspace 可见的已发布 App 工具（builtin community/knowledge + DB published）。

    经 ai_native_app_registry.list_published_manifests（已合并 builtin + DB）；
    每个 capability → AppTool。零 fake：无发布 manifest → []。
    """
    from backend.app.hasn_core.app_platform import ai_native_app_registry
    from backend.database.db import async_db_session

    tools: list[AppTool] = []
    async with async_db_session() as db:
        manifests = await ai_native_app_registry.list_published_manifests(db)
    seen: set[str] = set()
    for manifest in manifests:
        for tool in build_app_tools_from_manifest(manifest):
            if tool.name in seen:
                continue
            seen.add(tool.name)
            tools.append(tool)
    return tools
