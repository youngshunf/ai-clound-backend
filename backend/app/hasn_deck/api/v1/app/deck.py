"""演示文稿用户端 API。

路由前缀: /api/v1/deck/app
认证方式: Owner JWT（owner hasn_id 由登录用户解析）。访问控制 = 产物级有效权限（应用平台 v3 §6.5），
不再是 owner 硬隔离——被共享的产物也可见 / 可编辑（按 permission）。
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.hasn_core import hasn_humans_dao
from backend.app.hasn_deck.service.deck_service import Subject, deck_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


class CreateDeckRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255, description='标题')
    topic: str | None = Field(default=None, description='原始主题/brief')
    language: str = Field(default='zh', description='主语言')
    source: str = Field(default='manual', description='来源 agent/manual/imported')
    style_profile_id: str | None = Field(default=None, description='引用的 StyleProfile slug')
    bound_agent_id: str | None = Field(default=None, description='协作分身 HASN ID（创建即绑定）')


class UpdateDeckRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    topic: str | None = None
    status: str | None = Field(default=None, description='状态 draft/generating/ready/archived')
    language: str | None = None
    # outline 是自由 JSON 列（无 schema 强制），两形态并存：canonical 数组 OutlineItem[]
    # （设计 01 契约 / webui / daemon 本地镜像 normalize_outline 归一后的形状，daemon→云端 sync 推的就是它）
    # 与历史对象 {items:[...]}（云端 MCP 工具 outline.set 写路径）。故这里必须同时接受 list/dict，
    # 只收 dict 会让 daemon 推数组时报 422「输入应为有效的字典」→ deck 永久同步失败。
    outline: list | dict | None = None
    design_contract: dict | None = None
    style_profile_id: str | None = None
    cover_asset_id: str | None = None
    bound_agent_id: str | None = Field(default=None, description='协作分身 HASN ID（改绑）')


class CreatePageRequest(BaseModel):
    position: int = Field(ge=0, description='页序（0 起）')
    title: str = Field(default='', max_length=255)
    html: str = Field(default='')
    notes: str | None = None
    layout_intent: str | None = None
    status: str = Field(default='empty', description='状态 empty/generating/generated/edited')


class UpdatePageRequest(BaseModel):
    position: int | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, max_length=255)
    html: str | None = None
    notes: str | None = None
    layout_intent: str | None = None
    status: str | None = None
    render_state: dict | None = None
    thumb_asset_id: str | None = None
    expected_version: int | None = Field(default=None, description='页级乐观锁：客户端持有的 rev')


class SetVisibilityRequest(BaseModel):
    visibility: str = Field(description='private/enterprise/link')
    enterprise_id: int | None = Field(default=None, description='设企业可见时归属的企业 ID')


class AddShareRequest(BaseModel):
    grantee_type: str = Field(description='human/agent/enterprise')
    grantee_id: str = Field(description='被授权对象 ID')
    permission: str = Field(description='viewer/editor/manager')


async def _resolve_owner(db: CurrentSession, request: Request) -> str:
    """登录用户 → HASN 主人 hasn_id。"""
    human = await hasn_humans_dao.get_by_user_id(db, request.user.id)
    if not human:
        raise errors.NotFoundError(msg='用户 HASN 身份不存在')
    return human.hasn_id


@router.post('/decks', summary='创建演示文稿', dependencies=[DependsJwtAuth])
async def create_deck(request: Request, db: CurrentSessionTransaction, body: CreateDeckRequest) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.create_deck(
        db,
        owner_id=owner_id,
        title=body.title,
        topic=body.topic,
        language=body.language,
        source=body.source,
        style_profile_id=body.style_profile_id,
        bound_agent_id=body.bound_agent_id,
    )
    return response_base.success(data=data)


@router.get('/decks', summary='演示文稿列表（我的 ∪ 共享给我的 ∪ 企业可见）', dependencies=[DependsJwtAuth])
async def list_decks(request: Request, db: CurrentSession, limit: int = 50, offset: int = 0) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.list_accessible_decks(db, subject=Subject.human(owner_id), limit=limit, offset=offset)
    return response_base.success(data=data)


@router.get('/style-profiles', summary='可复用样式列表（系统内置 37 风格 ∪ 我的自定义）', dependencies=[DependsJwtAuth])
async def list_style_profiles(request: Request, db: CurrentSession) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.list_style_profiles(db, owner_id=owner_id)
    return response_base.success(data=data)


@router.get('/decks/{deck_id}', summary='演示文稿详情', dependencies=[DependsJwtAuth])
async def get_deck(request: Request, db: CurrentSession, deck_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.get_deck(db, subject=Subject.human(owner_id), deck_id=deck_id)
    return response_base.success(data=data)


@router.put('/decks/{deck_id}', summary='更新演示文稿', dependencies=[DependsJwtAuth])
async def update_deck(
    request: Request, db: CurrentSessionTransaction, deck_id: int, body: UpdateDeckRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.update_deck(db, subject=Subject.human(owner_id), deck_id=deck_id, fields=body.model_dump())
    return response_base.success(data=data)


@router.delete('/decks/{deck_id}', summary='删除演示文稿（软删）', dependencies=[DependsJwtAuth])
async def delete_deck(request: Request, db: CurrentSessionTransaction, deck_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    await deck_service.delete_deck(db, subject=Subject.human(owner_id), deck_id=deck_id)
    return response_base.success()


# ---------- 共享管理（仅 manager 权） ----------


@router.get('/decks/{deck_id}/shares', summary='查看产物共享名单', dependencies=[DependsJwtAuth])
async def list_shares(request: Request, db: CurrentSession, deck_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.list_shares(db, subject=Subject.human(owner_id), deck_id=deck_id)
    return response_base.success(data=data)


@router.put(
    '/decks/{deck_id}/visibility',
    summary='设置可见性（私有/企业可见/链接）',
    dependencies=[DependsJwtAuth],
    name='deck_app_set_visibility',
)
async def set_visibility(
    request: Request, db: CurrentSessionTransaction, deck_id: int, body: SetVisibilityRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.set_visibility(
        db, subject=Subject.human(owner_id), deck_id=deck_id, visibility=body.visibility, enterprise_id=body.enterprise_id
    )
    return response_base.success(data=data)


@router.post('/decks/{deck_id}/shares', summary='添加/更新协作者（人/分身/企业）', dependencies=[DependsJwtAuth])
async def add_share(
    request: Request, db: CurrentSessionTransaction, deck_id: int, body: AddShareRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.add_share(
        db,
        subject=Subject.human(owner_id),
        deck_id=deck_id,
        grantee_type=body.grantee_type,
        grantee_id=body.grantee_id,
        permission=body.permission,
    )
    return response_base.success(data=data)


@router.delete('/decks/{deck_id}/shares', summary='撤销协作者', dependencies=[DependsJwtAuth])
async def revoke_share(
    request: Request, db: CurrentSessionTransaction, deck_id: int, grantee_type: str, grantee_id: str
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    ok = await deck_service.revoke_share(
        db, subject=Subject.human(owner_id), deck_id=deck_id, grantee_type=grantee_type, grantee_id=grantee_id
    )
    return response_base.success(data={'revoked': ok})


# ---------- pages ----------


@router.get('/decks/{deck_id}/pages', summary='幻灯片列表', dependencies=[DependsJwtAuth])
async def list_pages(request: Request, db: CurrentSession, deck_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.list_pages(db, subject=Subject.human(owner_id), deck_id=deck_id)
    return response_base.success(data=data)


@router.post('/decks/{deck_id}/pages', summary='新增幻灯片', dependencies=[DependsJwtAuth])
async def create_page(
    request: Request, db: CurrentSessionTransaction, deck_id: int, body: CreatePageRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.create_page(
        db,
        subject=Subject.human(owner_id),
        deck_id=deck_id,
        position=body.position,
        title=body.title,
        html=body.html,
        notes=body.notes,
        layout_intent=body.layout_intent,
        status=body.status,
    )
    return response_base.success(data=data)


@router.put('/pages/{page_id}', summary='更新幻灯片', dependencies=[DependsJwtAuth])
async def update_page(
    request: Request, db: CurrentSessionTransaction, page_id: int, body: UpdatePageRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    fields = body.model_dump(exclude={'expected_version'})
    data = await deck_service.update_page(
        db, subject=Subject.human(owner_id), page_id=page_id, fields=fields, expected_version=body.expected_version
    )
    return response_base.success(data=data)


@router.delete('/pages/{page_id}', summary='删除幻灯片（软删）', dependencies=[DependsJwtAuth])
async def delete_page(request: Request, db: CurrentSessionTransaction, page_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    await deck_service.delete_page(db, subject=Subject.human(owner_id), page_id=page_id)
    return response_base.success()
