"""平台工具 · memory 域（doc16 Phase C1：记忆工具迁云端权威）。

把分身的记忆工具从「只在本地 hasn-node `memory.db`」迁到**云端 platform MCP 工具**：分身
（云端 / 本地 runtime 统一）经 `/api/v1/mcp/streamable` 直达云端，工具体 in-process 直调
云端权威 `semantic_fact_service`，读写 `hasn_memory.semantic_fact`（单一云端记忆，doc16）。

四工具（与 doc16 §6 C1 一致）：
- `hasn.memory.save`：分身主动记一条语义事实（默认 `agent_self` 自身记忆；可记 owner/peer/world）。
  写类 → `memory:write`（出厂 Allow，owner 三态可覆盖、事后可改可删）。三态由 `server.call_tool`
  统一判定（维度①），工具体不二次校验。
- `hasn.memory.search`：按文本搜索 owner 名下 active 事实。读类，无 scope。
- `hasn.memory.recall`：按主体/作用域召回 active 事实（供注入）。读类，无 scope。
- `hasn.memory.list`：分页列 owner 名下 active 事实。读类，无 scope。

owner 隔离由 Agent JWT/MCP Key 解析出的 `agent_context.owner_hasn_id` 强制；分身身份
（`agent_context.hasn_id`）作 `agent_self` 事实的 agent_id，二者皆**绝不入请求体**。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn_memory.service.semantic_fact_service import semantic_fact_service
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext

NAMESPACE = 'hasn.memory'
SCOPE_WRITE = 'memory:write'


def _s(desc: str) -> dict[str, Any]:
    return {'type': 'string', 'description': desc}


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {'type': 'object', 'properties': props}
    if required:
        schema['required'] = required
    return schema


class _MemorySaveTool(BaseTool):
    """hasn.memory.save — 分身记一条语义事实（写类，memory:write）。"""

    @property
    def name(self) -> str:
        return f'{NAMESPACE}.save'

    @property
    def namespace(self) -> str:
        return NAMESPACE

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '记一条长期语义事实到云端权威记忆。predicate 是谓词（如「偏好」「擅长」「目标」），'
            'object 是其值（任意 JSON）。**主体（subject）按谁的事实来填**：'
            '① 关于你自己 → subject_kind=agent_self（默认，自动推断，无需 subject_id）；'
            '② 关于主人 → subject_kind=owner（自动推断，无需 subject_id）；'
            '③ 关于对端分身本身 → subject_kind=peer + subject_id=对端分身的 a_ 开头 hasn_id；'
            '④ 关于对端分身的主人 / 群里某人 / 任何第三方 → subject_kind=peer + subject_id=那个人的 h_ 开头 hasn_id'
            '（对端主人的 h_id 见系统提示的对端主人身份段；群里成员的 hasn_id 见群名册）；'
            '⑤ 世界知识 → subject_kind=world + scope。'
            '**铁律：在对任何人回一句「已记录 / 我记住了」之前，必须已真实调用本工具把它落库——'
            '禁止只口头应承却不存，那是空头支票、日后谁都找不回来。**'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return _schema(
            {
                'predicate': _s('谓词（必填，如「偏好咖啡」「常用工具」「阶段目标」）'),
                'object': {'description': '对象/取值（必填，任意 JSON：字符串/数字/对象/数组）'},
                'subject_kind': _s('主体类型：agent_self（默认，自身记忆）| owner（关于主人）| peer | world'),
                'subject_id': _s('可选：peer/world 主体 id（agent_self/owner 自动推断，无需传）'),
                'confidence': {'type': 'number', 'description': '可选：置信度 0-1（默认 0.6）'},
                'scope_kind': _s('可选：作用域 global（默认）/workspace/project/task/conversation/topic'),
                'scope_id': _s('可选：作用域 id'),
                'rationale': _s('可选：记录理由（为什么知道这条）'),
            },
            ['predicate', 'object'],
        )

    @property
    def required_scopes(self) -> list[str]:
        return [SCOPE_WRITE]

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        async with async_db_session.begin() as db:
            return await semantic_fact_service.save_fact(
                db,
                owner_id=agent_context.owner_hasn_id or '',
                agent_id=agent_context.hasn_id,
                predicate=arguments['predicate'],
                object_value=arguments.get('object'),
                subject_kind=arguments.get('subject_kind') or 'agent_self',
                subject_id=arguments.get('subject_id'),
                confidence=arguments.get('confidence'),
                scope_kind=arguments.get('scope_kind') or 'global',
                scope_id=arguments.get('scope_id'),
                rationale=arguments.get('rationale'),
            )


class _MemorySearchTool(BaseTool):
    """hasn.memory.search — 文本搜索 owner 名下 active 事实（读类，无 scope）。"""

    @property
    def name(self) -> str:
        return f'{NAMESPACE}.search'

    @property
    def namespace(self) -> str:
        return NAMESPACE

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '按关键词搜索已记的语义事实（在谓词与取值上模糊匹配）。确定性读，无需授权。'
            '**被问到某人的事一时答不上来时，先用本工具（或 recall）按那人的 hasn_id 召回**——'
            '关于他的资料可能已被你或同主人的其它分身存过，别急着说不知道或臆造。'
            '可传 subject_id 限定某个人（如某 peer 的 hasn_id）做「按人 + 关键词」组合检索。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return _schema(
            {
                'query': _s('搜索关键词（必填）'),
                'subject_kind': _s('可选：限定主体类型 agent_self/owner/peer/world'),
                'subject_id': _s('可选：限定某主体 id（如某 peer 的 hasn_id）——按人 + 关键词组合检索'),
                'limit': {'type': 'integer', 'description': '可选：返回上限（默认 20，最大 200）'},
            },
            ['query'],
        )

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        async with async_db_session() as db:
            return await semantic_fact_service.search_facts(
                db,
                owner_id=agent_context.owner_hasn_id or '',
                query=arguments.get('query', ''),
                subject_kind=arguments.get('subject_kind'),
                subject_id=arguments.get('subject_id'),
                limit=int(arguments.get('limit', 20)),
            )


class _MemoryRecallTool(BaseTool):
    """hasn.memory.recall — 按主体/作用域召回 active 事实（读类，无 scope）。"""

    @property
    def name(self) -> str:
        return f'{NAMESPACE}.recall'

    @property
    def namespace(self) -> str:
        return NAMESPACE

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '召回某主体/作用域下的语义事实（按更新时间倒序，供随上下文注入）。确定性读，无需授权。'
            '**要答某人的偏好/近况/背景却想不起来时，先按其 hasn_id（subject_id）召回**——'
            '关于他的记忆可能已被你或同主人的其它分身存过，别凭空回答。'
            '可加 query 词项做「按人 + 关键词」召回。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return _schema({
            'subject_kind': _s('可选：主体类型 agent_self/owner/peer/world'),
            'subject_id': _s('可选：主体 id（如某 peer 的 hasn_id）'),
            'query': _s('可选：关键词（谓词/取值 ILIKE 模糊匹配）——按人 + 关键词组合召回'),
            'scope_kind': _s('可选：作用域 global/workspace/project/task/conversation/topic'),
            'scope_id': _s('可选：作用域 id'),
            'min_confidence': {'type': 'number', 'description': '可选：最低置信度过滤（默认 0）'},
            'limit': {'type': 'integer', 'description': '可选：返回上限（默认 50，最大 200）'},
        })

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        async with async_db_session() as db:
            return await semantic_fact_service.recall_facts(
                db,
                owner_id=agent_context.owner_hasn_id or '',
                subject_kind=arguments.get('subject_kind'),
                subject_id=arguments.get('subject_id'),
                query=arguments.get('query'),
                scope_kind=arguments.get('scope_kind'),
                scope_id=arguments.get('scope_id'),
                min_confidence=float(arguments.get('min_confidence', 0.0)),
                limit=int(arguments.get('limit', 50)),
            )


class _MemoryListTool(BaseTool):
    """hasn.memory.list — 分页列 owner 名下 active 事实（读类，无 scope）。"""

    @property
    def name(self) -> str:
        return f'{NAMESPACE}.list'

    @property
    def namespace(self) -> str:
        return NAMESPACE

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return '分页列出已记的语义事实（默认全主体，可按 subject_kind 过滤）。确定性读，无需授权。'

    @property
    def input_schema(self) -> dict[str, Any]:
        return _schema({
            'subject_kind': _s('可选：限定主体类型 agent_self/owner/peer/world'),
            'limit': {'type': 'integer', 'description': '可选：每页条数（默认 50，最大 200）'},
            'offset': {'type': 'integer', 'description': '可选：偏移（默认 0）'},
        })

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        async with async_db_session() as db:
            return await semantic_fact_service.list_facts(
                db,
                owner_id=agent_context.owner_hasn_id or '',
                subject_kind=arguments.get('subject_kind'),
                limit=int(arguments.get('limit', 50)),
                offset=int(arguments.get('offset', 0)),
            )


MEMORY_TOOLS: list[BaseTool] = [
    _MemorySaveTool(),
    _MemorySearchTool(),
    _MemoryRecallTool(),
    _MemoryListTool(),
]
