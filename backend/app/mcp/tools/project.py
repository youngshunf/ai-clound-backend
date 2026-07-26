"""平台工具 · project 域（项目管理，模块 14 doc38）。

分身经 `/api/v1/mcp/streamable` 直达云端，工具体直调云端权威 `project_service` / 挂靠点注册表
（in-process，不经 daemon HTTP relay）。owner 隔离由凭证解析出的 `agent_context.owner_hasn_id`
强制，身份绝不入请求体。

9 个工具（doc38 §3 item 5）：
- `hasn.project.create/get/list/update/link/unlink`
- `hasn.project.milestone.create/update/complete`

三态闸门由 `server.call_tool` 统一判定（维度①），工具体不二次校验。写类经 `async_db_session.begin()`
自动提交 + best-effort WSPUSH `project` 失效；读类走 `async_db_session()`。

铁律遵循：
- **封面只收 `hasn://asset/{id}` 引用**（禁 base64/字节/URL 直链，父仓 CLAUDE.md 工具入参铁律）；
- **link/unlink 一律经挂靠点注册表**（`project_linkage_registry`），project service 不散写跨 schema UPDATE；
- **register-on-write**：`create` 建行后即把项目登记进 `hasn_artifacts`（公共接缝，绑当次工作会话）；
- **产物流并集读**：`get` 聚合 = `project_id` 直接命中 ∪ 挂靠容器名下产物（读时派生不回填）。
"""

from __future__ import annotations

import logging

from typing import Any

from backend.app.hasn_project.service.project_app_service import project_service
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.app.mcp.artifact_registration import merge_resource_uri, register_app_resource_artifact
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.common.exception import errors
from backend.database.db import async_db_session

logger = logging.getLogger(__name__)

NAMESPACE = 'hasn.project'
SCOPE_READ = 'project:read'
SCOPE_WRITE = 'project:write'


# ── helpers ──────────────────────────────────────────────────────────────────
def _without(args: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in args.items() if k not in keys}


def _owner_hasn_id(ctx: AgentContext) -> str:
    """收紧“所有分身必须有主人”不变量，避免可空认证字段流入项目 owner 边界。"""
    owner = ctx.owner_hasn_id
    if not owner:
        raise RuntimeError('project tool: Agent 主人身份缺失')
    return owner


def _validate_cover(uri: Any) -> None:
    """封面入参守卫：只接受 `hasn://asset/{id}` 引用（空=不设置）。禁 base64/字节/URL 直链（铁律）。"""
    if uri is None:
        return
    text = str(uri).strip()
    if text == '':
        return
    if not text.startswith('hasn://asset/'):
        raise errors.RequestError(
            msg='封面图只接受 hasn://asset/{id} 引用——请先经 hasn.image.generate（AI 生成）或 '
            'hasn.stock.download（素材下载）产出资产，再把返回的 asset 引用写进项目（禁 base64/URL 直链）',
            data={'error_code': 'invalid_cover'},
        )


async def _safe_bump(db: Any, owner_hasn_id: str | None) -> None:
    """业务提交后 → WSPUSH `hasn.sync.invalidate(project)` 给该 owner 在线节点（best-effort）。

    U5 会在 `sync_invalidate_service` 正式加 `KIND_PROJECT` 常量 + daemon `project` kind 处理；
    此处用 getattr 兜底，缺常量时回落字面量 `'project'`，云端不识别即 no-op/warn，不影响业务写。
    """
    if not owner_hasn_id:
        return
    try:
        from backend.app.hasn.service import sync_invalidate_service as siv

        kind = getattr(siv, 'KIND_PROJECT', 'project')
        await siv.bump_owner(kind, db, owner_hasn_id)
    except Exception as e:
        logger.warning('[project] platform tool sync invalidate 推送失败 (非致命): %s', e)


async def _safe_linkage_bump(
    db: Any,
    *,
    owner_hasn_id: str,
    resource_uri: str,
) -> None:
    """挂靠提交后发布资源域失效信号；失败只告警，不影响已经提交的业务。"""
    try:
        await project_linkage_registry.bump_sync_after_commit(
            db,
            owner=owner_hasn_id,
            resource_uri=resource_uri,
        )
    except Exception as e:
        logger.warning('[project] platform tool linked resource sync invalidate 推送失败 (非致命): %s', e)


# ── handlers ─────────────────────────────────────────────────────────────────
async def _h_create(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """建项目（name 必填，可选 goal/cover_asset_uri/bound_agent_id）：建行后 register-on-write 直登记。"""
    _validate_cover(args.get('cover_asset_uri'))
    owner = _owner_hasn_id(ctx)
    result = await project_service.create_project(db, owner=owner, data=args)
    registration = await register_app_resource_artifact(
        db,
        app_id='project',
        resource_kind='project',
        server_id=str(result['id']),
        agent_hasn_id=ctx.agent_hasn_id,
        owner_hasn_id=owner,
        title=str(result.get('name') or '项目'),
        summary=str(result.get('goal') or '') or None,
        source_tool=f'{NAMESPACE}.create',
    )
    return merge_resource_uri(result, registration)


async def _h_get(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """取项目详情（含里程碑轨）+ 产物流并集读（直接命中 ∪ 挂靠容器名下产物）。"""
    owner = _owner_hasn_id(ctx)
    detail = await project_service.get_project(db, owner=owner, pk=args['id'])
    detail['artifact_flow'] = await project_service.project_artifact_flow(
        db, owner=owner, project_id=args['id']
    )
    return detail


async def _h_list(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """列主人名下项目（可选按 status 过滤 active/archived）。"""
    return await project_service.list_projects(db, owner=_owner_hasn_id(ctx), status=args.get('status'))


async def _h_update(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """改项目（name/goal/封面/状态/绑分身；status=archived 即归档，v1 只归档不硬删）。"""
    _validate_cover(args.get('cover_asset_uri'))
    return await project_service.update_project(
        db,
        owner=_owner_hasn_id(ctx),
        pk=args['id'],
        data=_without(args, 'id'),
    )


async def _h_link(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """把资源挂靠进项目（经挂靠点注册表落挂靠列）：先校验目标项目归属，再由 adapter 写列。"""
    project_id = args['project_id']
    owner = _owner_hasn_id(ctx)
    await project_service.assert_owned(db, owner=owner, pk=project_id)
    return await project_linkage_registry.link(
        db, owner=owner, resource_uri=args['resource_uri'], project_id=project_id
    )


async def _h_unlink(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """把资源从项目摘出（经挂靠点注册表把挂靠列置 NULL）。"""
    return await project_linkage_registry.unlink(
        db,
        owner=_owner_hasn_id(ctx),
        resource_uri=args['resource_uri'],
    )


async def _h_milestone_create(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """在项目下建里程碑（name 必填；纯业务态标记，无依赖无门控）。"""
    return await project_service.create_milestone(
        db,
        owner=_owner_hasn_id(ctx),
        project_id=args['project_id'],
        data=_without(args, 'project_id'),
    )


async def _h_milestone_update(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """改里程碑（名/到期/状态/关联产物/排序）：传 id + 要改字段。"""
    return await project_service.update_milestone(
        db,
        owner=_owner_hasn_id(ctx),
        milestone_id=int(args['id']),
        data=_without(args, 'id'),
    )


async def _h_milestone_complete(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """完成里程碑（status→done）：纯业务态标记，不触发任何门控/依赖检查。"""
    return await project_service.complete_milestone(
        db,
        owner=_owner_hasn_id(ctx),
        milestone_id=int(args['id']),
    )


# ── schema 小工具 ─────────────────────────────────────────────────────────────
def _s(desc: str) -> dict[str, Any]:
    return {'type': 'string', 'description': desc}


def _i(desc: str) -> dict[str, Any]:
    return {'type': 'integer', 'description': desc}


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {'type': 'object', 'properties': props}
    if required:
        out['required'] = required
    return out


# ── 工具规格（action → name=hasn.project.<action>）──────────────────────────────
_SPECS: list[dict[str, Any]] = [
    {
        'action': 'create',
        'write': True,
        'handler': _h_create,
        'desc': (
            '建平台项目（「为了哪件事」的业务容器）：name 必填；可选 goal(一句话目标)、'
            'cover_asset_uri(封面，只收 hasn://asset/{id} 引用)、bound_agent_id(默认协作分身)。'
            '建成即登记为产物并返回 hasn://project/{id}，主人可在产物 tab / 会话资源栏点开。'
        ),
        'schema': _schema(
            {
                'name': _s('项目名（必填）'),
                'goal': _s('可选：一句话目标（供聚合视图与派发上下文注入）'),
                'cover_asset_uri': _s('可选：封面图资产引用 hasn://asset/{id}（禁 base64/URL 直链）'),
                'bound_agent_id': _s('可选：默认协作分身 hasn_id（owner 名下 a_* 分身）'),
            },
            ['name'],
        ),
    },
    {
        'action': 'get',
        'write': False,
        'handler': _h_get,
        'desc': (
            '取项目详情：基本信息 + 里程碑轨(milestones) + 产物流并集读(artifact_flow)。传 id。'
            'artifact_flow = 本项目**全链路**产物（跨执行、跨应用、跨分身，含 artifact_id 与 hasn:// 深链）——'
            '要知道「这个项目到目前为止各环产了什么」用它；只要本次工作流执行的用 hasn.workflow.run_artifacts。'
        ),
        'schema': _schema({'id': _s('项目 id（云端权威 UUID，必填）')}, ['id']),
    },
    {
        'action': 'list',
        'write': False,
        'handler': _h_list,
        'desc': '列主人名下项目（active 在前）。可选 status 过滤 active(进行中)/archived(已归档)。',
        'schema': _schema({'status': _s('可选：按状态过滤 active|archived')}),
    },
    {
        'action': 'update',
        'write': True,
        'handler': _h_update,
        'desc': (
            '改项目：传 id + 要改字段（name/goal/cover_asset_uri/bound_agent_id/status）。'
            'status=archived 即归档（v1 只归档、不硬删）；封面只收 hasn://asset/{id}。'
        ),
        'schema': _schema(
            {
                'id': _s('项目 id（必填）'),
                'name': _s('可选：项目名'),
                'goal': _s('可选：一句话目标'),
                'cover_asset_uri': _s('可选：封面图 hasn://asset/{id}'),
                'bound_agent_id': _s('可选：默认协作分身 hasn_id'),
                'status': _s('可选：active(进行中)|archived(已归档，即归档)'),
            },
            ['id'],
        ),
    },
    {
        'action': 'link',
        'write': True,
        'handler': _h_link,
        'desc': (
            '把一个资源挂靠进项目（联邦挂靠）：传 project_id + resource_uri(hasn:// 资源地址)。'
            '当前支持 hasn://artifact/{id}（把产物显式改挂到本项目，覆盖自动打标）；'
            '知识库/deck/图坊项目等容器挂靠随后续版本开放。'
        ),
        'schema': _schema(
            {
                'project_id': _s('目标项目 id（云端权威 UUID，必填）'),
                'resource_uri': _s('要挂靠的资源 hasn:// 地址，如 hasn://artifact/{id}（必填）'),
            },
            ['project_id', 'resource_uri'],
        ),
    },
    {
        'action': 'unlink',
        'write': True,
        'handler': _h_unlink,
        'desc': '把一个资源从项目摘出：传 resource_uri(hasn:// 资源地址)，把其项目挂靠置空。',
        'schema': _schema(
            {'resource_uri': _s('要摘出的资源 hasn:// 地址（必填）')},
            ['resource_uri'],
        ),
    },
    {
        'action': 'milestone.create',
        'write': True,
        'handler': _h_milestone_create,
        'desc': (
            '在项目下建里程碑（纯业务状态标记，无依赖边无门控）：传 project_id + name；'
            '可选 due_time(到期，RFC3339)、artifact_ref(关联产物引用)、sort(排序)。'
        ),
        'schema': _schema(
            {
                'project_id': _s('所属项目 id（必填）'),
                'name': _s('里程碑名（必填）'),
                'due_time': _s('可选：到期时间（RFC3339；逾期由读时按当前时间派生，不落库状态）'),
                'artifact_ref': _s('可选：关联产物引用（hasn:// 资源或 artifact_id）'),
                'sort': _i('可选：排序（里程碑轨横向次序）'),
            },
            ['project_id', 'name'],
        ),
    },
    {
        'action': 'milestone.update',
        'write': True,
        'handler': _h_milestone_update,
        'desc': '改里程碑：传 id + 要改字段（name/due_time/status/artifact_ref/sort）。',
        'schema': _schema(
            {
                'id': _i('里程碑 id（必填）'),
                'name': _s('可选：里程碑名'),
                'due_time': _s('可选：到期时间（RFC3339）'),
                'status': _s('可选：pending(待完成)|done(已完成)'),
                'artifact_ref': _s('可选：关联产物引用'),
                'sort': _i('可选：排序'),
            },
            ['id'],
        ),
    },
    {
        'action': 'milestone.complete',
        'write': True,
        'handler': _h_milestone_complete,
        'desc': '完成里程碑（status→done）：传 id。纯业务态标记，不触发门控/依赖检查。',
        'schema': _schema({'id': _i('里程碑 id（必填）')}, ['id']),
    },
]


class _ProjectTool(BaseTool):
    """project 域单 struct + spec 派发（避免 9 个同形态类样板，对齐 plan/deck 云端平台工具范式）。"""

    def __init__(self, spec: dict[str, Any]) -> None:
        self._action = spec['action']
        self._name = f'{NAMESPACE}.{spec["action"]}'
        self._desc = spec['desc']
        self._input_schema = spec['schema']
        self._write = bool(spec['write'])
        self._handler = spec['handler']

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> str:
        return NAMESPACE

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return self._desc

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def required_scopes(self) -> list[str]:
        # 写类 = project:write、读类 = project:read（均 Allow 出厂，doc38 §3-5）。
        return [SCOPE_WRITE] if self._write else [SCOPE_READ]

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        # 维度① 三态由 server.call_tool 统一判定，工具内不二次校验。
        if self._write:
            # service 写方法只 flush 不 commit → 用 .begin() 自动提交；退出后才发布同步失效。
            owner_hasn_id = _owner_hasn_id(agent_context)
            async with async_db_session.begin() as db:
                result = await self._handler(db, agent_context, arguments)
            async with async_db_session() as sync_db:
                await _safe_bump(sync_db, owner_hasn_id)
                if self._action in {'link', 'unlink'} and result.get('changed'):
                    await _safe_linkage_bump(
                        sync_db,
                        owner_hasn_id=owner_hasn_id,
                        resource_uri=result['resource_uri'],
                    )
            return result
        async with async_db_session() as db:
            return await self._handler(db, agent_context, arguments)


PROJECT_TOOLS: list[_ProjectTool] = [_ProjectTool(spec) for spec in _SPECS]
