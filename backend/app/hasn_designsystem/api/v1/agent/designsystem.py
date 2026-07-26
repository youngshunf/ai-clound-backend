"""设计系统生成应用 Agent 端 API。

路由前缀: /api/v1/designsystem/agent
认证方式: Agent JWT（身份取自 JWT claims，绝不读请求体身份）。
owner 隔离：分身代表主人行动 → Subject.agent(agent_hasn_id, owner_hasn_id)；可见域 builtin∪owner∪enterprise。
scope 闸（与 scopes.py / hasn-mcp 声明对齐）：
- 读类（list/get/revisions/owner-revision）**无 required scope**（确定性读，不设假闸门）；
- 写类（save/delete/import）→ designsystem:write（出厂 Allow）；
- 协作绑定（collaborator）→ designsystem:publish（按需，默认 Ask）。
"""

import logging

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_designsystem.service.design_system_service import Subject, design_system_service
from backend.app.hasn_designsystem.service.import_service import import_design_source
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_capability import require_capability_not_denied
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction, async_db_session

router = APIRouter()
log = logging.getLogger(__name__)

_SCOPE_WRITE = 'designsystem:write'
_SCOPE_PUBLISH = 'designsystem:publish'


def _subject(agent: AgentTokenPayload) -> Subject:
    return Subject.agent(agent.agent_hasn_id, agent.owner_hasn_id)


async def _bump_designsystem_sync(db: AsyncSession, owner_hasn_id: str) -> None:
    """设计系统写点（save/delete）后 → WSPUSH ``hasn.sync.invalidate(designsystem)`` 给该 owner 在线节点。

    在线 daemon 秒级对账本地镜像（read_through 回填），离线节点靠重连 ``hasn.connected`` 握手对账。
    owner-scoped 推送：只扰动改动者本人的在线设备，其它 owner 在重连时凭全局 revision 自然追平
    （设计 doc14/实施12 P5「online 全局 push 是侵入操作」之缓解）。best-effort，推送失败绝不影响写入。
    """
    try:
        from backend.app.hasn.service.sync_invalidate_service import KIND_DESIGNSYSTEM
        from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

        await sync_bump(KIND_DESIGNSYSTEM, db, owner_id=owner_hasn_id)
    except Exception as e:  # 推送 best-effort
        log.warning('[designsystem] sync invalidate 推送失败 (非致命): %s', e)


async def _publish_designsystem_sync_after_commit(owner_hasn_id: str) -> None:
    """设计系统权威写提交后，用独立会话发布同步指纹。

    ``design_system_service`` 的写方法会自行提交；路由不能把它放进
    ``CurrentSessionTransaction`` 的 ``begin()`` 上下文，也不能在提交后继续复用已关闭的事务。
    """
    try:
        async with async_db_session() as sync_db:
            await _bump_designsystem_sync(sync_db, owner_hasn_id)
    except Exception as e:  # 推送 best-effort
        log.warning('[designsystem] post-commit sync invalidate 发布失败 (非致命): %s', e)


class SaveDesignSystemRequest(BaseModel):
    design_system_id: int | None = Field(default=None, description='存量 id（None=新建）')
    slug: str = Field(min_length=1, max_length=128, description='owner 内唯一短名')
    name: str = Field(min_length=1, max_length=128, description='展示名')
    content: dict[str, Any] = Field(
        description='四层 token 契约产物：tokens_css/design_tokens_json/tailwind_css/design_md/'
        'components_html/components_manifest_json/token_contract_report_json'
    )
    category: str | None = Field(default=None, max_length=48)
    source_kind: str = Field(
        default='generated', max_length=32, description='generated/imported_shadcn/imported_github/...'
    )
    score: int | None = Field(default=None, ge=0, le=100)
    grade: str | None = Field(default=None, max_length=16)
    recommend_rebuild: bool = False
    bundle_asset_id: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=512)
    enterprise_id: int | None = None
    required_scenes: list[str] | None = Field(
        default=None,
        description='组件画廊要求覆盖的场景 id 列表（None=不改；owner 派发时设定，分身透传）',
    )
    platform_project_id: str | None = Field(
        default=None,
        description='新建设计系统挂靠的平台项目 id（可空=不挂靠）',
    )


class AddCollaboratorRequest(BaseModel):
    agent_hasn_id: str = Field(min_length=1, max_length=64, description='被绑定的协作分身 hasn_id')


class ImportRequest(BaseModel):
    source: str = Field(description='shadcn | github | screenshot | url')
    ref: str = Field(min_length=1, description='registry item URL / owner/repo[#branch] / 页面 URL')


# ── 设计系统：建/查/改/删 ─────────────────────────────────────────────────────
@router.post('/design-systems', summary='创建或更新设计系统（落一版 revision）')
async def agent_save_design_system(
    db: CurrentSession, body: SaveDesignSystemRequest, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_WRITE)
    data = await design_system_service.save(
        db,
        subject=_subject(agent),
        design_system_id=body.design_system_id,
        slug=body.slug,
        name=body.name,
        content=body.content,
        category=body.category,
        source_kind=body.source_kind,
        score=body.score,
        grade=body.grade,
        recommend_rebuild=body.recommend_rebuild,
        bundle_asset_id=body.bundle_asset_id,
        note=body.note,
        enterprise_id=body.enterprise_id,
        required_scenes=body.required_scenes,
        platform_project_id=body.platform_project_id,
    )
    await _publish_designsystem_sync_after_commit(agent.owner_hasn_id)
    return response_base.success(data=data)


@router.get('/design-systems', summary='分身可见的设计系统（builtin∪owner∪enterprise）')
async def agent_list_design_systems(
    db: CurrentSession,
    agent: AgentTokenPayload = DependsAgentJwtAuth,
    category: Annotated[str | None, Query()] = None,
    enterprise_id: Annotated[int | None, Query()] = None,
    platform_project_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResponseModel:
    data = await design_system_service.list_visible(
        db,
        viewer_owner_hasn_id=agent.owner_hasn_id,
        enterprise_id=enterprise_id,
        category=category,
        platform_project_id=platform_project_id,
        limit=limit,
        offset=offset,
    )
    return response_base.success(data=data)


@router.get('/design-systems/{design_system_id}', summary='设计系统详情（含当前版本内容）')
async def agent_get_design_system(
    db: CurrentSession,
    design_system_id: Annotated[int, Path(ge=1)],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
    enterprise_id: Annotated[int | None, Query()] = None,
) -> ResponseModel:
    data = await design_system_service.get(
        db, design_system_id=design_system_id, viewer_owner_hasn_id=agent.owner_hasn_id, enterprise_id=enterprise_id
    )
    return response_base.success(data=data)


@router.delete('/design-systems/{design_system_id}', summary='软删设计系统（仅 owner，非 builtin）')
async def agent_delete_design_system(
    db: CurrentSession,
    design_system_id: Annotated[int, Path(ge=1)],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_WRITE)
    await design_system_service.delete(db, design_system_id=design_system_id, owner_hasn_id=agent.owner_hasn_id)
    await _publish_designsystem_sync_after_commit(agent.owner_hasn_id)
    return response_base.success()


# ── 版本历史 ──────────────────────────────────────────────────────────────────
@router.get('/design-systems/{design_system_id}/revisions', summary='版本历史（降序）')
async def agent_list_revisions(
    db: CurrentSession,
    design_system_id: Annotated[int, Path(ge=1)],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    data = await design_system_service.list_revisions(
        db, design_system_id=design_system_id, viewer_owner_hasn_id=agent.owner_hasn_id
    )
    return response_base.success(data=data)


@router.get('/revisions/{revision_id}', summary='单版本完整内容')
async def agent_get_revision(
    db: CurrentSession, revision_id: Annotated[int, Path(ge=1)], agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    data = await design_system_service.get_revision(
        db, revision_id=revision_id, viewer_owner_hasn_id=agent.owner_hasn_id
    )
    return response_base.success(data=data)


@router.get('/owner-revision', summary='owner 维度同步水位（content-hash 聚合 revision）')
async def agent_owner_revision(db: CurrentSession, agent: AgentTokenPayload = DependsAgentJwtAuth) -> ResponseModel:
    rev = await design_system_service.compute_owner_revision(db, owner_hasn_id=agent.owner_hasn_id)
    return response_base.success(data={'owner_revision': rev})


# ── 导入三入口（DS-P3）：产 tokens.css 草稿交分身 compile ──────────────────────
@router.post('/import', summary='导入 shadcn/github/screenshot → tokens.css 草稿（草稿≠最终）')
async def agent_import(
    body: ImportRequest, db: CurrentSession, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_WRITE)
    data = await import_design_source(body.source, body.ref)
    return response_base.success(data=data)


# ── 协作分身绑定（DECKBIND 对齐）──────────────────────────────────────────────
@router.get('/design-systems/{design_system_id}/collaborators', summary='协作分身列表')
async def agent_list_collaborators(
    db: CurrentSession,
    design_system_id: Annotated[int, Path(ge=1)],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    data = await design_system_service.list_collaborators(
        db, design_system_id=design_system_id, viewer_owner_hasn_id=agent.owner_hasn_id
    )
    return response_base.success(data=data)


@router.post('/design-systems/{design_system_id}/collaborators', summary='绑定协作分身（owner 名下）')
async def agent_add_collaborator(
    db: CurrentSessionTransaction,
    design_system_id: Annotated[int, Path(ge=1)],
    body: AddCollaboratorRequest,
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_PUBLISH)
    data = await design_system_service.add_collaborator(
        db, design_system_id=design_system_id, owner_hasn_id=agent.owner_hasn_id, agent_hasn_id=body.agent_hasn_id
    )
    return response_base.success(data=data)
