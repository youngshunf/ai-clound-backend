"""平台工具 · artifact 域（产物化 P6，分身显式登记一条产物）。

把分身的 `hasn.artifact.record` 从 hasn-node 本地 hasn-mcp 迁到**云端 platform MCP 工具**
（不操作本地文件/数据 → 走云端，与 contact/message/plan/notification 同范式）：分身经
`/api/v1/mcp/streamable` 直达云端，工具体直调云端权威 `hasn_artifacts_service.record`
（in-process，**不再**经 daemon → `/api/v1/artifacts/agent/record` HTTP relay）。

与 hasn-mcp `audited` 的 best-effort 自动捕获（其它工具产物的副作用，仍留本地）不同，这是分身
**主动**把一份成果落库——尤其用于**文本/markdown 产物直接入库**（`kind=document, body=<markdown>`，
不转文件上传）。本体三选一（互斥）：`body` / `asset_id` / `resource_uri`。带 `origin_ref`
（如 `resource:plan:todo:{id}`）则可被该业务对象详情页的产物轨按 origin 反查（P6 §6.5）。

身份恒由 `agent_context`（取自 Agent JWT/MCP Key）注入，body 绝不含 agent/owner；云端按白名单
归一 kind、按 `(agent, dispatch_id, asset_id)` 去重幂等。

- 工具名 + input_schema 与原 hasn-mcp `ArtifactRecordTool` **逐字段 1:1**。
- 与本地工具一致：**不声明 capability scope**（低风险账本写、身份钉死本主人）；三态闸门由
  `server.call_tool` 统一判定（出厂 Allow），工具体不二次校验。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn.schema.hasn_artifacts import RecordArtifactParam
from backend.app.hasn.service.hasn_artifacts_service import hasn_artifacts_service
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from backend.app.hasn.schema.hasn_artifacts import ArtifactDetail, ArtifactItem
    from backend.app.mcp.auth import AgentContext

_ARTIFACT_KINDS = ['image', 'voice', 'video', 'file', 'document', 'deck', 'webpage', 'dataset', 'other']
_SOURCE_KINDS = ['task_result', 'tool_output', 'upload', 'external']

# Agent 面出参裁剪常量（有别于 owner 端点：省分身上下文，见设计 §3.1.5）。
_LIST_SIZE_DEFAULT = 10  # agent 列表默认页大小（比 owner 面收紧）
_LIST_SIZE_MAX = 30  # agent 列表页大小封顶
_SUMMARY_MAX = 200  # 列表项 summary 截断长度
_BODY_MAX = 20000  # 详情 body 截断长度（覆盖绝大多数 document）

# 防呆描述（§7）：出参里的短时效签名 URL 不得进正文/对外发布物。
_PREVIEW_URL_WARNING = (
    'preview_url 是短时效预览链接（约 1 小时过期），禁止写入文章正文或对外发布物；'
    '正文嵌图一律用 ![](hasn://asset/{id})（即出参的 asset_uri）。'
)

# 分页入参 schema（list/search 复用，避免长行重复）。
_PAGE_PROP = {'type': 'integer', 'description': '页码（默认 1）'}
_SIZE_PROP = {
    'type': 'integer',
    'description': f'页大小（默认 {_LIST_SIZE_DEFAULT}，封顶 {_LIST_SIZE_MAX}）',
}


def _truncate(text: str | None, limit: int) -> str | None:
    """长字段封顶截断（None 透传）。"""
    if not text:
        return text
    return text if len(text) <= limit else text[:limit]


def _project_list_item(item: ArtifactItem) -> dict[str, Any]:
    """列表/搜索出参投影：剥离 body 全文（以 has_body 替代）、summary 截断、给 asset_uri。

    与 owner 端点最大差异——ArtifactItem 自带 body 全文（document 类是整篇 markdown），
    agent 列表 20 条足以撑爆上下文，必须剥掉。
    """
    asset_uri = f'hasn://asset/{item.asset_id}' if item.asset_id else None
    return {
        'artifact_id': item.artifact_id,
        'kind': item.kind,
        'title': item.title,
        'summary': _truncate(item.summary, _SUMMARY_MAX),
        'asset_uri': asset_uri,  # 有 asset 本体时给，正文嵌图用它
        'preview_url': item.display_url,  # 短时效签名 URL，仅供预览，勿入正文
        'resource_uri': item.resource_uri,  # deck/webpage 类给
        'has_body': bool(item.body),  # 文本类产物标记，正文用 artifact.get 取
        'source_tool': item.source_tool,
        'source_kind': item.source_kind,
        'created_time': item.created_time.isoformat() if item.created_time else None,
    }


class ArtifactRecordTool(BaseTool):
    """`hasn.artifact.record`：分身显式登记一条产物到统一产物表（cloud-hosted，P6）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.artifact.record'

    @property
    def namespace(self) -> str:
        return 'hasn.artifact'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '把一份成果显式登记到统一产物表（产物化 P6）：文本/markdown 用 kind=document + body 直接入库'
            '（不上传文件），二进制用 asset_id，资源用 resource_uri（三选一）。带 origin_ref'
            '（如 resource:plan:todo:{id}）则该业务对象详情页的产物轨可按 origin 反查。'
            '身份恒为本 Agent（云端按凭证注入），归本人主人所有。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'kind': {
                    'type': 'string',
                    'enum': _ARTIFACT_KINDS,
                    'description': '产物类型（见 enum）；文本/markdown 用 document',
                },
                'title': {'type': 'string', 'description': '展示标题（可选，<=200 字）'},
                'summary': {'type': 'string', 'description': '简要描述（可选）'},
                'body': {
                    'type': 'string',
                    'description': (
                        '文本/markdown 正文（document 文本产物用，直接入库不上传文件；与 asset_id/resource_uri 三选一）'
                    ),
                },
                'asset_id': {'type': 'string', 'description': '已上传资产 ID（image/voice/file 二进制产物用）'},
                'resource_uri': {'type': 'string', 'description': 'hasn:// 资源 URI（deck/webpage 等本体即资源时用）'},
                'origin_ref': {
                    'type': 'string',
                    'description': '产出所属业务资源（如 resource:plan:todo:{id}），供该对象详情产物轨反查',
                },
                'source_kind': {
                    'type': 'string',
                    'enum': _SOURCE_KINDS,
                    'description': '产出来源（默认 task_result）',
                },
            },
            'required': ['kind'],
        }

    @property
    def required_scopes(self) -> list[str]:
        # 与本地 hasn-mcp 工具 1:1：不声明独立 capability scope（低风险账本写、身份钉死本主人）。
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        """整形入参 → 云端 hasn_artifacts_service.record（in-process，自动提交）。

        维度① 能力授权由 server.call_tool 三态 mode 统一判定（D3），工具内不二次校验。
        """

        def _str(key: str) -> str | None:
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            return None

        kind = _str('kind')
        if not kind:
            raise RuntimeError("artifact.record: 'kind' 必填")

        body = _str('body')
        asset_id = _str('asset_id')
        resource_uri = _str('resource_uri')
        # 本体三选一（互斥）：文本 body / 二进制 asset_id / 资源 resource_uri，至少其一。
        if not body and not asset_id and not resource_uri:
            raise RuntimeError('artifact.record: 必须带 body、asset_id 或 resource_uri 其一（文本走 body 不上传文件）')

        params = RecordArtifactParam(
            kind=kind,
            title=_str('title'),
            summary=_str('summary'),
            body=body,
            asset_id=asset_id,
            resource_uri=resource_uri,
            origin_ref=_str('origin_ref'),
            # 绑当次工作会话：取系统注入的 `_hasn_session_id`（server.call_tool 已剥进 agent_context，
            # 分身不可伪造）。漏传会让产物只进分身产物 tab、挂不进工作会话资源栏——主人在会话里
            # 看不到分身刚干的活（2026-07-15 实测：record 成功、artifact.get 取得到，资源栏却空）。
            session_id=agent_context.session_id,
            # 显式登记默认归「任务成果」（区别于工具副作用 tool_output）。
            source_kind=_str('source_kind') or 'task_result',
            source_tool='hasn.artifact.record',
        )

        # 写类 → .begin() 自动提交（service 只 flush 不 commit）。
        async with async_db_session.begin() as db:
            artifact_id = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent_context.agent_hasn_id,
                owner_hasn_id=agent_context.owner_hasn_id,
                params=params,
            )
        return {'artifact_id': artifact_id}


def _clamp_page_size(arguments: dict[str, Any]) -> tuple[int, int]:
    """归一分页参数：page>=1；size 默认 _LIST_SIZE_DEFAULT、封顶 _LIST_SIZE_MAX。"""
    try:
        page = int(arguments.get('page') or 1)
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(arguments.get('size') or _LIST_SIZE_DEFAULT)
    except (TypeError, ValueError):
        size = _LIST_SIZE_DEFAULT
    page = max(1, page)
    size = max(1, min(size, _LIST_SIZE_MAX))
    return page, size


class ArtifactListTool(BaseTool):
    """`hasn.artifact.list`：列**本分身**的产物时间线（按 kind/session 过滤、分页）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.artifact.list'

    @property
    def namespace(self) -> str:
        return 'hasn.artifact'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '列我（本分身）产过的资源时间线（图/语音/视频/文件/文档/deck/网页等），倒序，'
            '可按 kind、session_id（某工作会话）过滤、分页。找回自己造过的东西用它。'
            '出参给 asset_uri（有本体时，正文嵌图用它）+ preview_url（临时预览）+ has_body'
            '（文本产物标记，取正文用 hasn.artifact.get）。' + _PREVIEW_URL_WARNING
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'kind': {
                    'type': 'string',
                    'enum': _ARTIFACT_KINDS,
                    'description': '按产物类型过滤（可选）',
                },
                'session_id': {
                    'type': 'string',
                    'description': '只看某工作会话产出的（可选，找我这个任务里产的东西）',
                },
                'page': _PAGE_PROP,
                'size': _SIZE_PROP,
            },
        }

    @property
    def required_scopes(self) -> list[str]:
        # 读类，与 artifact.record 一致：不声明独立 capability scope（出厂 Allow）。
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        """本分身产物时间线 → list_by_agent（owner+agent 隔离）→ 工具层投影出参（剥 body）。"""
        page, size = _clamp_page_size(arguments)
        kind = (arguments.get('kind') or '').strip() or None
        session_id = (arguments.get('session_id') or '').strip() or None
        async with async_db_session() as db:
            items, total = await hasn_artifacts_service.list_by_agent(
                db,
                owner_hasn_id=agent_context.owner_hasn_id,
                agent_hasn_id=agent_context.agent_hasn_id,
                page=page,
                size=size,
                kind=kind,
                session_id=session_id,
            )
        return {
            'items': [_project_list_item(it) for it in items],
            'total': total,
            'page': page,
            'size': size,
        }


class ArtifactSearchTool(BaseTool):
    """`hasn.artifact.search`：按关键词（title/summary 子串）搜**本分身**的产物。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.artifact.search'

    @property
    def namespace(self) -> str:
        return 'hasn.artifact'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '按关键词搜我产过的资源（匹配 title/summary 子串，多词空格分隔=全部命中），'
            '可再按 kind、session_id 过滤、分页。写图文文章找现成配图先用它。'
            '出参同 hasn.artifact.list（asset_uri 正文嵌图用）。' + _PREVIEW_URL_WARNING
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': '关键词（必填，空格分隔多词=全部命中）'},
                'kind': {
                    'type': 'string',
                    'enum': _ARTIFACT_KINDS,
                    'description': '按产物类型过滤（可选）',
                },
                'session_id': {'type': 'string', 'description': '只看某工作会话产出的（可选）'},
                'page': {'type': 'integer', 'description': '页码（默认 1）'},
                'size': {
                    'type': 'integer',
                    'description': f'页大小（默认 {_LIST_SIZE_DEFAULT}，封顶 {_LIST_SIZE_MAX}）',
                },
            },
            'required': ['query'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        """关键词搜本分身产物 → list_by_agent(keyword=...) → 同 list 投影出参。"""
        query = (arguments.get('query') or '').strip()
        if not query:
            raise RuntimeError("artifact.search: 'query' 必填")
        page, size = _clamp_page_size(arguments)
        kind = (arguments.get('kind') or '').strip() or None
        session_id = (arguments.get('session_id') or '').strip() or None
        async with async_db_session() as db:
            items, total = await hasn_artifacts_service.list_by_agent(
                db,
                owner_hasn_id=agent_context.owner_hasn_id,
                agent_hasn_id=agent_context.agent_hasn_id,
                page=page,
                size=size,
                kind=kind,
                keyword=query,
                session_id=session_id,
            )
        return {
            'items': [_project_list_item(it) for it in items],
            'total': total,
            'page': page,
            'size': size,
        }


def _project_detail(detail: ArtifactDetail) -> dict[str, Any]:
    """详情出参投影：body 截断（附 body_truncated）、给 asset_uri + preview_url。"""
    body = detail.body
    body_truncated = bool(body and len(body) > _BODY_MAX)
    return {
        'artifact_id': detail.artifact_id,
        'kind': detail.kind,
        'title': detail.title,
        'summary': detail.summary,
        'body': _truncate(body, _BODY_MAX),
        'body_truncated': body_truncated,
        'asset_uri': f'hasn://asset/{detail.asset_id}' if detail.asset_id else None,
        'preview_url': detail.display_url,  # 短时效签名 URL
        'resource_uri': detail.resource_uri,
        'origin_ref': detail.origin_ref,
        'conversation_id': detail.conversation_id,
        'message_id': detail.message_id,
        'session_id': detail.session_id,
        'source_tool': detail.source_tool,
        'source_kind': detail.source_kind,
        'metadata': detail.metadata,
        'created_time': detail.created_time.isoformat() if detail.created_time else None,
    }


class ArtifactGetTool(BaseTool):
    """`hasn.artifact.get`：取单条产物详情（含 body/引用/溯源）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.artifact.get'

    @property
    def namespace(self) -> str:
        return 'hasn.artifact'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '取一条产物的详情（含正文 body、asset_uri、resource_uri、溯源信息）。'
            '要基于历史产物改写、或取文本产物全文时用它。同主人任意分身的产物均可读'
            '（支撑分身间复用）。' + _PREVIEW_URL_WARNING
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'artifact_id': {'type': 'string', 'description': '产物 ID（art_…）'},
            },
            'required': ['artifact_id'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        """按 owner 隔离取详情（同主人任意分身的产物均可读）→ body 截断投影。"""
        artifact_id = (arguments.get('artifact_id') or '').strip()
        if not artifact_id:
            raise RuntimeError("artifact.get: 'artifact_id' 必填")
        async with async_db_session() as db:
            detail = await hasn_artifacts_service.get_detail(
                db,
                owner_hasn_id=agent_context.owner_hasn_id,
                artifact_id=artifact_id,
            )
        return _project_detail(detail)


ARTIFACT_TOOLS: list[BaseTool] = [
    ArtifactRecordTool(),
    ArtifactListTool(),
    ArtifactSearchTool(),
    ArtifactGetTool(),
]
