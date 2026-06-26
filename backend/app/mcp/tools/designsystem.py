"""平台工具 · designsystem 域（设计系统生成应用，DS-P4 的云端权威 4 工具）。

把分身的 `hasn.designsystem.{import,save,list,get}` 从 hasn-node 本地 hasn-mcp 迁到**云端
platform MCP 工具**（不操作本地文件/数据 → 走云端，与 contact/message/plan/notification/artifact
同范式）：分身经 `/api/v1/mcp/streamable` 直达云端，工具体直调云端权威 `design_system_service`
/ `import_design_source`（in-process，**不再**经 daemon → `/api/v1/designsystem/agent/*` HTTP relay，
list/get 亦不再经 daemon `DesignSystemGateway` 本地镜像）。

保留在本地 hasn-mcp 的是 4 个**确定性纯函数**工具（`compile_tokens`/`derive`/`validate`/
`extract_components`，直调 `hasn_designsystem_core`、真离线可跑、无云端/无 daemon）——它们才真正
「操作本地数据/无需云端」，符合留本地的判据。

身份恒由 `agent_context`（取自 Agent JWT/MCP Key）注入：
- save → `Subject.agent(agent_hasn_id, owner_hasn_id)`（owner 隔离 + 协作权限由 service 强制）；
- list/get → `viewer_owner_hasn_id=owner_hasn_id`（可见域 builtin∪owner∪企业∪共享）。

- 工具名 + input_schema 与原 hasn-mcp `designsystem` 云端工具 **逐字段 1:1**（分身引用不变）。
- scope 与本地 1:1：写类 import/save 声明 `designsystem:write`（出厂 Allow）；读类 list/get 无 scope。
  三态闸门由 `server.call_tool` 统一判定，工具体不二次校验。
- save 成功落版后 best-effort 登记一条指向 bundle 资产的可下载产物（AF/D2，对齐原 daemon
  `capture_best_effort`）——独立事务、失败只 warn，绝不影响 save 结果；无 bundle 资产则不臆造。
"""

from __future__ import annotations

import logging

from typing import TYPE_CHECKING, Any

from backend.app.hasn.schema.hasn_artifacts import RecordArtifactParam
from backend.app.hasn.service.hasn_artifacts_service import hasn_artifacts_service
from backend.app.hasn_designsystem.service.design_system_service import Subject, design_system_service
from backend.app.hasn_designsystem.service.import_service import import_design_source
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext

log = logging.getLogger(__name__)

_SCOPE_WRITE = 'designsystem:write'


async def _bump_designsystem_sync(db: Any, owner_hasn_id: str) -> None:
    """设计系统写点（save）后 → WSPUSH ``hasn.sync.invalidate(designsystem)`` 给该 owner 在线节点。

    在线 daemon 秒级对账本地镜像（read_through 回填），离线节点靠重连握手对账。best-effort，
    推送失败绝不影响写入（与 agent API `_bump_designsystem_sync` 同逻辑）。
    """
    try:
        from backend.app.hasn.service.sync_invalidate_service import KIND_DESIGNSYSTEM
        from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

        await sync_bump(KIND_DESIGNSYSTEM, db, owner_id=owner_hasn_id)
    except Exception as e:  # 推送 best-effort
        log.warning('[designsystem] sync invalidate 推送失败 (非致命): %s', e)


def _str(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _req_str(arguments: dict[str, Any], key: str) -> str:
    value = _str(arguments, key)
    if not value:
        raise RuntimeError(f"designsystem: '{key}' 必填且非空")
    return value


def _opt_int(arguments: dict[str, Any], key: str) -> int | None:
    value = arguments.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class DesignSystemImportTool(BaseTool):
    """`hasn.designsystem.import`：shadcn/github/screenshot/url → tokens.css 草稿（cloud-hosted）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.import'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '从 shadcn registry / GitHub 仓 / 截图URL 扫 CSS 自定义属性，产出初始 tokens.css 草稿'
            '（给分身打底，非最终；交 compile_tokens 标准化）。云端拉取含 SSRF 闸。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'source': {'type': 'string', 'description': '来源类型：shadcn | github | screenshot | url'},
                'ref': {'type': 'string', 'description': 'registry item URL / owner/repo[#branch] / 页面 URL'},
            },
            'required': ['source', 'ref'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return [_SCOPE_WRITE]

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        source = _req_str(arguments, 'source')
        ref = _req_str(arguments, 'ref')
        # 纯拉取（无 db 写）：云端含 SSRF 闸；身份不参与（草稿不落库）。
        return await import_design_source(source, ref)


class DesignSystemSaveTool(BaseTool):
    """`hasn.designsystem.save`：组装完整 bundle → 写 hasn_designsystem 表 + 落一版 revision（cloud-hosted）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.save'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '组装完整 bundle → 写云端 hasn_designsystem 表 + 落一版 revision + 注册供下游 picker。'
            '返回 {id, revision, content_hash, name, score, grade}。非幂等（每次落新版本）。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'design_system_id': {'type': 'integer', 'description': '可选：存量设计系统 id（缺省=新建）'},
                'slug': {'type': 'string', 'description': 'owner 内唯一短名'},
                'name': {'type': 'string', 'description': '展示名'},
                'content': {
                    'type': 'object',
                    'description': (
                        '四层 token 契约产物对象：{tokens_css, design_tokens_json, tailwind_css, '
                        'design_md, components_html, components_manifest_json, token_contract_report_json}'
                    ),
                },
                'category': {'type': 'string', 'description': '可选：品类'},
                'source_kind': {
                    'type': 'string',
                    'description': '可选：generated / imported_shadcn / imported_github / ...（缺省 generated）',
                },
                'score': {'type': 'integer', 'description': '可选：0–100 评分'},
                'grade': {'type': 'string', 'description': '可选：等级'},
            },
            'required': ['slug', 'name', 'content'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return [_SCOPE_WRITE]

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        slug = _req_str(arguments, 'slug')
        name = _req_str(arguments, 'name')
        content = arguments.get('content')
        if not content or not isinstance(content, dict):
            raise RuntimeError("designsystem.save: 'content' 必填（四层契约产物对象）")

        recommend = arguments.get('recommend_rebuild')
        subject = Subject.agent(agent_context.agent_hasn_id, agent_context.owner_hasn_id)

        # design_system_service.save 内部 self-commit（区别于 plan/artifact 的「只 flush」约定），
        # 故用**普通 session**（非 begin() 上下文管理器）——后者会被 service 的内部 commit 关闭其事务、
        # 令随后的 bump 撞 “closed transaction” 守卫。save 自提交后 bump 在新自起事务里写、末尾显式提交。
        async with async_db_session() as db:
            data = await design_system_service.save(
                db,
                subject=subject,
                design_system_id=_opt_int(arguments, 'design_system_id'),
                slug=slug,
                name=name,
                content=content,
                category=_str(arguments, 'category'),
                source_kind=_str(arguments, 'source_kind') or 'generated',
                score=_opt_int(arguments, 'score'),
                grade=_str(arguments, 'grade'),
                recommend_rebuild=recommend if isinstance(recommend, bool) else False,
                bundle_asset_id=_str(arguments, 'bundle_asset_id'),
                note=_str(arguments, 'note'),
                enterprise_id=_opt_int(arguments, 'enterprise_id'),
            )
            await _bump_designsystem_sync(db, agent_context.owner_hasn_id)
            await db.commit()

        # save 已落库后：best-effort 登记 bundle 产物（独立事务，失败只 warn，不影响 save 结果）。
        await self._record_bundle_artifact_best_effort(agent_context, data)
        return data

    @staticmethod
    async def _record_bundle_artifact_best_effort(agent_context: AgentContext, data: dict[str, Any]) -> None:
        """save 携 revision.bundle_asset_id → 登记一条 asset 类（document）可下载产物 + 语义元数据。

        对齐原 hasn-mcp daemon `capture_best_effort`：无 bundle 资产则不登记（零 fake，不臆造资源指针）；
        设计系统**不是**已注册的 hasn:// 资源域，故以 asset 为本体（不产 resource 指针）。
        """
        revision = data.get('revision') if isinstance(data.get('revision'), dict) else {}
        bundle_asset_id = revision.get('bundle_asset_id')
        if not isinstance(bundle_asset_id, str) or not bundle_asset_id.strip():
            return

        metadata: dict[str, Any] = {}
        for key in ('slug', 'grade'):
            if isinstance(data.get(key), str):
                metadata[key] = data[key]
        if isinstance(data.get('id'), int):
            metadata['design_system_id'] = data['id']
        if isinstance(data.get('score'), int):
            metadata['score'] = data['score']
        if isinstance(revision.get('rev_no'), int):
            metadata['rev_no'] = revision['rev_no']

        params = RecordArtifactParam(
            kind='document',
            title=data.get('name') if isinstance(data.get('name'), str) else None,
            asset_id=bundle_asset_id.strip(),
            source_kind='tool_output',
            source_tool='hasn.designsystem.save',
            metadata=metadata,
        )
        try:
            async with async_db_session.begin() as db:
                await hasn_artifacts_service.record(
                    db,
                    agent_hasn_id=agent_context.agent_hasn_id,
                    owner_hasn_id=agent_context.owner_hasn_id,
                    params=params,
                )
        except Exception as e:  # 产物登记 best-effort
            log.warning('[designsystem] bundle 产物登记失败 (非致命): %s', e)


class DesignSystemListTool(BaseTool):
    """`hasn.designsystem.list`：列出分身可见的设计系统（builtin∪owner∪企业∪共享，cloud-hosted）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.list'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return '列出分身可见的设计系统（builtin∪owner∪企业∪共享）。返回 {items, total}。确定性读。'

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'category': {'type': 'string', 'description': '可选：按品类过滤'},
                'enterprise_id': {'type': 'integer', 'description': '可选：企业域'},
                'limit': {'type': 'integer', 'description': '可选：页大小（默认 50，上限 200）'},
                'offset': {'type': 'integer', 'description': '可选：偏移（默认 0）'},
            },
        }

    @property
    def required_scopes(self) -> list[str]:
        # 读类无 scope（确定性读，不设假闸门）。
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        limit = _opt_int(arguments, 'limit') or 50
        limit = max(1, min(limit, 200))
        offset = _opt_int(arguments, 'offset') or 0
        async with async_db_session() as db:
            return await design_system_service.list_visible(
                db,
                viewer_owner_hasn_id=agent_context.owner_hasn_id,
                enterprise_id=_opt_int(arguments, 'enterprise_id'),
                category=_str(arguments, 'category'),
                limit=limit,
                offset=max(0, offset),
            )


class DesignSystemGetTool(BaseTool):
    """`hasn.designsystem.get`：取一套设计系统详情（含当前版本完整内容，cloud-hosted）。"""

    @property
    def name(self) -> str:
        return 'hasn.designsystem.get'

    @property
    def namespace(self) -> str:
        return 'hasn.designsystem'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '取一套设计系统详情（含当前版本 tokens.css / design-tokens.json / tailwind / design.md / '
            'components / 契约报告）。确定性读。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'design_system_id': {'type': 'integer', 'description': '设计系统 id'},
                'enterprise_id': {'type': 'integer', 'description': '可选：企业域'},
            },
            'required': ['design_system_id'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        design_system_id = _opt_int(arguments, 'design_system_id')
        if design_system_id is None or design_system_id < 1:
            raise RuntimeError("designsystem.get: 'design_system_id' 必填且 ≥ 1")
        async with async_db_session() as db:
            return await design_system_service.get(
                db,
                design_system_id=design_system_id,
                viewer_owner_hasn_id=agent_context.owner_hasn_id,
                enterprise_id=_opt_int(arguments, 'enterprise_id'),
            )


DESIGNSYSTEM_TOOLS: list[BaseTool] = [
    DesignSystemImportTool(),
    DesignSystemSaveTool(),
    DesignSystemListTool(),
    DesignSystemGetTool(),
]
