"""MCP Tool Directory and progressive exposure projection."""

from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from backend.app.mcp.tool_exposure import tool_exposure_policy

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext
    from backend.app.mcp.tools.base import BaseTool
    from backend.app.mcp.tools.registry import ToolRegistry

ToolSource = Literal['platform', 'app', 'local', 'external']


@dataclass(frozen=True)
class ToolSearchQuery:
    query: str
    source: str = 'all'
    detail: str = 'summary'
    page_size: int = 20
    cursor: str | None = None


class ToolDirectoryService:
    """Builds discovery/search projections from the full invocation registry."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def list_bootstrap_tools(self, agent_context: AgentContext) -> list[dict[str, Any]]:
        return [
            self._tool_schema(tool)
            for tool in self._registry.list_bootstrap_tools()
            if self._can_discover(agent_context, tool)
        ]

    def list_all_tools(self, agent_context: AgentContext) -> list[dict[str, Any]]:
        """legacy_all 暴露（设计 08 §6.2）：列出该 agent 可见的全部工具（platform + 已加载 app）。

        function-calling Runtime（hermes-agent）只认 `tools/list` 返回的工具、不支持
        「search 后轮内二次注入工具声明」，故对这类运行时直接全量暴露。三态 deny 的工具
        不出现（与 `_can_discover` 一致）；ask 仍可见（调用时由 ask 闸门挂起）。
        """
        return [
            self._tool_schema(tool)
            for tool in self._registry.get_all_tools()
            if self._can_discover(agent_context, tool)
        ]

    async def search(
        self,
        agent_context: AgentContext,
        search_query: ToolSearchQuery,
    ) -> dict[str, Any]:
        query = search_query.query.strip()
        visible_tools = [tool for tool in self._registry.get_all_tools() if self._can_discover(agent_context, tool)]

        if query == 'sources' or search_query.detail == 'sources':
            return {
                'workspace_key': self._workspace_key(agent_context),
                'query': query,
                'sources': self._source_index(visible_tools),
                'tools': [],
                'schemas': [],
                'next_cursor': None,
                'trace_id': self._trace_id(agent_context, query),
            }

        if query == 'apps':
            return {
                'workspace_key': self._workspace_key(agent_context),
                'query': query,
                'sources': [],
                'tools': [],
                'schemas': [],
                'next_cursor': None,
                'trace_id': self._trace_id(agent_context, query),
            }

        matched_tools = self._match_tools(visible_tools, query, search_query.source)
        page_size = min(max(search_query.page_size, 1), 50)
        page = matched_tools[:page_size]
        has_next = len(matched_tools) > page_size

        return {
            'workspace_key': self._workspace_key(agent_context),
            'query': query,
            'sources': [],
            'tools': [] if search_query.detail == 'schema' else [self._tool_summary(tool) for tool in page],
            'schemas': [self._tool_schema(tool) for tool in page] if search_query.detail == 'schema' else [],
            'next_cursor': str(page_size) if has_next else None,
            'trace_id': self._trace_id(agent_context, query),
        }

    def build_scope_catalog(self, agent_context: AgentContext) -> dict[str, Any]:
        """聚合「全部可见工具」的 required_scopes → 按 source 分组 → 每条标三态 mode + 元数据。

        D2：catalog 列全部工具（含社交，一视同仁），按来源分组，每条带三态当前值。
        对象级关系门控（维度②）不进 catalog（工具运行时返回）。external 分组按本 Agent
        binding 派生（仅列其授权的第三方 MCP 工具，P7；未绑定则该分组为空）。
        """
        from backend.app.mcp.scopes import SOURCE_LABELS, scope_meta
        from backend.app.mcp.tool_exposure import tool_exposure_policy
        from backend.common.security.scope_policy import resolve_capability_mode

        default_mode = getattr(agent_context, 'default_mode', 'allow')
        capability_modes = getattr(agent_context, 'capability_modes', {}) or {}

        # source -> scope_key -> {tools: set, risk}
        grouped: dict[str, dict[str, dict[str, Any]]] = {'platform': {}, 'app': {}, 'external': {}}
        for tool in self._registry.get_all_tools():
            source = self._source_for_tool(tool)
            if source == 'local':
                continue  # 本地工具不在云端 catalog
            # 第四暴露面收编（doc18 §3.2）：按 G1/G2 硬边界剔除（per-agent 投影）——
            # 普通分身不列 diag:* 特权工具、也不列未绑定的 external 工具（含串号防护）。
            # G5 三态永不参与：deny 项须留在 catalog 供 owner 改回（不复刻 102-B3 单向门）。
            if tool_exposure_policy.is_catalog_hidden(agent_context, tool):
                continue
            bucket = grouped.setdefault(source, {})
            for scope in tool.required_scopes:
                entry = bucket.setdefault(scope, {'tools': set(), 'risk': getattr(tool, 'risk_level', 'low')})
                entry['tools'].add(tool.name)

        sources: list[dict[str, Any]] = []
        for source in ('platform', 'app', 'external'):
            capabilities = []
            for scope_key in sorted(grouped.get(source, {})):
                entry = grouped[source][scope_key]
                meta = scope_meta(scope_key)
                # 出厂默认成为唯一真相：每条能力的静息态由其 per-capability 出厂默认决定
                # （忽略全局 default_mode），与本地 CapabilityModeMirror 缺省回落 Rust 出厂默认一致。
                # owner 在 capability_modes 显式覆盖时优先；未覆盖则呈现出厂默认（花钱类如实显示「每次询问」）。
                factory_default = meta.get('default_mode', 'allow')
                capabilities.append({
                    'key': scope_key,
                    'label': meta['label'],
                    'domain': meta['domain'],
                    'risk': meta.get('risk') or entry.get('risk', 'low'),
                    'description': meta['description'],
                    'default_mode': factory_default,
                    'mode': resolve_capability_mode(factory_default, capability_modes, scope_key),
                    'tools': sorted(entry['tools']),
                })
            sources.append({
                'source': source,
                'label': SOURCE_LABELS.get(source, source),
                'capabilities': capabilities,
            })

        return {'default_mode': default_mode, 'sources': sources}

    def cloud_tool_namespaces(self) -> set[str]:
        """云端实际可达工具（platform + app，排除 local 源）的 namespace 集合。

        供 `tool.search` 描述判定哪些 `domain_summary` 域真在云端面可调：deck/task/workflow/
        reel/film/publish 等纯本地工具（manifest `tools=[]`、`transport_mode='local'`）不进云端
        注册表，其域不应在云端目录里被宣传成可搜——否则云端分身据此去搜/调会扑空、报「本地工具
        不可用」。plan/designsystem 虽 manifest 为 local，但其工具经 TOOLMIG 迁成 platform 工具、
        在册，故仍判为云端可达（如实保留）。
        """
        return {
            self._namespace_for_tool(tool)
            for tool in self._registry.get_all_tools()
            if self._source_for_tool(tool) != 'local'
        }

    def _match_tools(
        self,
        tools: list[BaseTool],
        query: str,
        source: str,
    ) -> list[BaseTool]:
        source_filtered = [tool for tool in tools if source in ('all', self._source_for_tool(tool))]

        if query.startswith('tool:'):
            tool_name = query.removeprefix('tool:')
            return [tool for tool in source_filtered if tool.name == tool_name]

        if query.startswith('app.'):
            namespace = 'hasn.' + query.removeprefix('app.')
            return [tool for tool in source_filtered if tool.name.startswith(f'{namespace}.')]

        if query.startswith('hasn.'):
            return [tool for tool in source_filtered if tool.name == query or tool.name.startswith(f'{query}.')]

        if query in {'platform', 'app', 'local', 'external'}:
            return [tool for tool in source_filtered if self._source_for_tool(tool) == query]

        # 模糊搜索：整串子串命中给强权重，再按「命中了几个词」加分。
        # 这样多词自然语言 query（如 "finance market quote"）不再因整串匹配不到而返回空，
        # 而是返回命中任一词的工具、命中词更多的排更前。单词 query 行为不变（所有命中者
        # 同分→稳定排序保持注册顺序，命中集与旧实现一致）。
        lowered = query.lower()
        terms = lowered.split()

        def _relevance(tool: BaseTool) -> int:
            haystack = f'{tool.name}\n{tool.description}'.lower()
            score = 10 if lowered in haystack else 0
            score += sum(1 for term in terms if term in haystack)
            return score

        scored = ((tool, _relevance(tool)) for tool in source_filtered)
        return [tool for tool, score in sorted(scored, key=lambda pair: -pair[1]) if score > 0]

    def _source_index(self, tools: list[BaseTool]) -> list[dict[str, Any]]:
        source_counts: dict[tuple[str, str], int] = {}
        for tool in tools:
            source = self._source_for_tool(tool)
            namespace = self._namespace_for_tool(tool)
            key = (source, namespace)
            source_counts[key] = source_counts.get(key, 0) + 1

        return [
            {
                'source': source,
                'namespace': namespace,
                'summary': self._source_summary(source, namespace),
                'visible_tool_count': count,
            }
            for (source, namespace), count in sorted(source_counts.items())
        ]

    def _tool_summary(self, tool: BaseTool) -> dict[str, Any]:
        schema_hash = self._schema_hash(tool.input_schema)
        return {
            'source': self._source_for_tool(tool),
            'name': tool.name,
            'title': tool.name.rsplit('.', maxsplit=1)[-1],
            'summary': tool.description,
            'required_scopes': tool.required_scopes,
            'risk_level': getattr(tool, 'risk_level', 'low'),
            'execution_location': self._execution_location_for_tool(tool),
            'idempotent': True,
            'schema_hash': schema_hash,
            'schema_ref': f'hasn://tool-schema/{tool.name}@{schema_hash}',
        }

    def _tool_schema(self, tool: BaseTool) -> dict[str, Any]:
        return {
            'source': self._source_for_tool(tool),
            'name': tool.name,
            'description': tool.description,
            'input_schema': tool.input_schema,
            'output_schema': getattr(tool, 'output_schema', {'type': 'object'}),
            'required_scopes': tool.required_scopes,
            'risk_level': getattr(tool, 'risk_level', 'low'),
            'execution_location': self._execution_location_for_tool(tool),
            'schema_hash': self._schema_hash(tool.input_schema),
        }

    def _can_discover(self, agent_context: AgentContext, tool: BaseTool) -> bool:
        # 统一暴露管线（doc18 §3·实施/103 U1）：发现面 = evaluate 非 HIDDEN 的投影。
        # external 白名单 / runtime 隐藏 / 三态 deny 全部收编进 ToolExposurePolicy，
        # ask 仍可见（调用时由 ask 闸门挂起）、VISIBLE_DENY（U3 付费墙）带引导列出。
        return tool_exposure_policy.evaluate(agent_context, tool).is_visible

    def _source_for_tool(self, tool: BaseTool) -> ToolSource:
        return getattr(tool, 'source', 'platform')

    def _execution_location_for_tool(self, tool: BaseTool) -> str:
        # P3: registration-time placement. Local-source tools default to local;
        # everything else to cloud, unless the tool declares otherwise.
        default = 'local' if self._source_for_tool(tool) == 'local' else 'cloud'
        return getattr(tool, 'execution_location', default)

    def _namespace_for_tool(self, tool: BaseTool) -> str:
        return getattr(tool, 'namespace', self._fallback_namespace(tool))

    def _fallback_namespace(self, tool: BaseTool) -> str:
        parts = tool.name.split('.')
        if len(parts) < 2:
            return tool.name
        if tool.name.startswith('hasn.ext.') and len(parts) >= 3:
            return '.'.join(parts[:3])
        return '.'.join(parts[:2])

    def _source_summary(self, source: str, namespace: str) -> str:
        if namespace == 'hasn.tool':
            return '工具发现与 schema 查询'
        if source == 'platform':
            return 'HASN 云端平台工具'
        if source == 'app':
            return '当前 workspace 可发现的 App 工具'
        if source == 'external':
            return '当前 Agent 已绑定的外部 MCP 工具'
        return '本地工具'

    def _schema_hash(self, schema: dict[str, Any]) -> str:
        canonical = json.dumps(schema, sort_keys=True, separators=(',', ':'))
        return 'sha256:' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    def _workspace_key(self, agent_context: AgentContext) -> str:
        workspace_key = agent_context.metadata.get('workspace_key') if agent_context.metadata else None
        return workspace_key or f'owner:{agent_context.owner_id}'

    def _trace_id(self, agent_context: AgentContext, query: str) -> str:
        raw = f'{agent_context.hasn_id}:{self._workspace_key(agent_context)}:{query}'
        return 'trace_' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
