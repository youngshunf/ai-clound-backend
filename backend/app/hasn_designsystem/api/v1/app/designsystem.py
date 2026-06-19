"""设计系统生成应用 Owner/WebUI 端 API（DS-P8）。

路由前缀: /api/v1/designsystem/app
认证方式: Owner JWT（owner hasn_id 由登录用户解析，绝不读请求体身份）。

定位：**daemon 的 webui-facing 读通道**——hasn-node daemon `domains/designsystem` 以 Owner JWT
（`BackendGateway::owner_transport`）回源这些端点，本地 SQLite 镜像做 read_through / local_first，
WebUI 只调 daemon（云端无关）。owner 是自己设计系统库的权威 → **读类无 scope 闸门**（与 agent
端一致：确定性读不设假闸门）；写类（删除）owner-only + WSPUSH 失效。

与 agent 端（`/api/v1/designsystem/agent/*`）共用同一套自定义 `design_system_service`，仅身份来源
不同：agent 端取自 Agent JWT claims（分身代主人），此处取自登录主人 Owner JWT。可见域同为
builtin∪owner∪enterprise。生成（save）由分身经 agent 通道落库——owner 端只浏览/查看/导入/删除，
不直接 save（Agent-Centric：能力在分身手里）。
"""

import logging

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_core import hasn_humans_dao
from backend.app.hasn_designsystem.service.design_system_service import design_system_service
from backend.app.hasn_designsystem.service.import_service import import_design_source
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()
log = logging.getLogger(__name__)


async def _resolve_owner(db: AsyncSession, request: Request) -> str:
    """登录用户 → HASN 主人 hasn_id。"""
    human = await hasn_humans_dao.get_by_user_id(db, request.user.id)
    if not human:
        raise errors.NotFoundError(msg='用户 HASN 身份不存在')
    return human.hasn_id


async def _bump_designsystem_sync(db: AsyncSession, owner_hasn_id: str) -> None:
    """owner 写点（删除）后 → WSPUSH ``hasn.sync.invalidate(designsystem)`` 给该 owner 在线节点。

    best-effort，推送失败绝不影响写入（与 agent 端 ``_bump_designsystem_sync`` 同范式）。
    """
    try:
        from backend.app.hasn.service.sync_invalidate_service import KIND_DESIGNSYSTEM
        from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

        await sync_bump(KIND_DESIGNSYSTEM, db, owner_id=owner_hasn_id)
    except Exception as e:  # 推送 best-effort
        log.warning('[designsystem] sync invalidate 推送失败 (非致命): %s', e)


class ImportRequest(BaseModel):
    source: str = Field(description='shadcn | github | screenshot | url')
    ref: str = Field(min_length=1, description='registry item URL / owner/repo[#branch] / 页面 URL')


# ── 设计系统：浏览 / 查看（read，无 scope 闸门）──────────────────────────────────
@router.get('/design-systems', summary='我可见的设计系统（builtin∪owner∪enterprise）', dependencies=[DependsJwtAuth])
async def app_list_design_systems(
    request: Request,
    db: CurrentSession,
    category: Annotated[str | None, Query()] = None,
    enterprise_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await design_system_service.list_visible(
        db,
        viewer_owner_hasn_id=owner,
        enterprise_id=enterprise_id,
        category=category,
        limit=limit,
        offset=offset,
    )
    return response_base.success(data=data)


@router.get(
    '/design-systems/{design_system_id}', summary='设计系统详情（含当前版本内容）', dependencies=[DependsJwtAuth]
)
async def app_get_design_system(
    request: Request,
    db: CurrentSession,
    design_system_id: Annotated[int, Path(ge=1)],
    enterprise_id: Annotated[int | None, Query()] = None,
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await design_system_service.get(
        db, design_system_id=design_system_id, viewer_owner_hasn_id=owner, enterprise_id=enterprise_id
    )
    return response_base.success(data=data)


@router.get('/design-systems/{design_system_id}/revisions', summary='版本历史（降序）', dependencies=[DependsJwtAuth])
async def app_list_revisions(
    request: Request,
    db: CurrentSession,
    design_system_id: Annotated[int, Path(ge=1)],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await design_system_service.list_revisions(db, design_system_id=design_system_id, viewer_owner_hasn_id=owner)
    return response_base.success(data=data)


@router.get('/revisions/{revision_id}', summary='单版本完整内容', dependencies=[DependsJwtAuth])
async def app_get_revision(
    request: Request, db: CurrentSession, revision_id: Annotated[int, Path(ge=1)]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await design_system_service.get_revision(db, revision_id=revision_id, viewer_owner_hasn_id=owner)
    return response_base.success(data=data)


@router.get(
    '/owner-revision', summary='owner 维度同步水位（content-hash 聚合 revision）', dependencies=[DependsJwtAuth]
)
async def app_owner_revision(request: Request, db: CurrentSession) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    rev = await design_system_service.compute_owner_revision(db, owner_hasn_id=owner)
    return response_base.success(data={'owner_revision': rev})


@router.get('/design-systems/{design_system_id}/collaborators', summary='协作分身列表', dependencies=[DependsJwtAuth])
async def app_list_collaborators(
    request: Request,
    db: CurrentSession,
    design_system_id: Annotated[int, Path(ge=1)],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await design_system_service.list_collaborators(
        db, design_system_id=design_system_id, viewer_owner_hasn_id=owner
    )
    return response_base.success(data=data)


# ── 导入三入口（DS-P3）：owner 在导入页粘参照 → 产 tokens.css 草稿交分身 compile ──────
@router.post(
    '/import', summary='导入 shadcn/github/screenshot → tokens.css 草稿（草稿≠最终）', dependencies=[DependsJwtAuth]
)
async def app_import(request: Request, db: CurrentSession, body: ImportRequest) -> ResponseModel:
    # owner 是自己库的权威，导入只产草稿（不落库）→ 无需 scope 闸；解析登录身份以拒绝匿名调用。
    await _resolve_owner(db, request)
    data = await import_design_source(body.source, body.ref)
    return response_base.success(data=data)


# ── 删除（owner-only，非 builtin）────────────────────────────────────────────────
@router.delete(
    '/design-systems/{design_system_id}', summary='软删设计系统（仅 owner，非 builtin）', dependencies=[DependsJwtAuth]
)
async def app_delete_design_system(
    request: Request,
    db: CurrentSessionTransaction,
    design_system_id: Annotated[int, Path(ge=1)],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    await design_system_service.delete(db, design_system_id=design_system_id, owner_hasn_id=owner)
    await _bump_designsystem_sync(db, owner)
    return response_base.success(data={'deleted': True})
