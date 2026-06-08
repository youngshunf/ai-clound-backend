"""演示文稿用户端 API。

路由前缀: /api/v1/deck/app
认证方式: Owner JWT（owner hasn_id 由登录用户解析），owner 隔离。
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.deck.service.deck_service import deck_service
from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
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


class UpdateDeckRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    topic: str | None = None
    status: str | None = Field(default=None, description='状态 draft/generating/ready/archived')
    language: str | None = None
    outline: dict | None = None
    design_contract: dict | None = None
    style_profile_id: str | None = None
    cover_asset_id: str | None = None


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


async def _resolve_owner(db: CurrentSession, request: Request) -> str:
    """登录用户 → HASN 主人 hasn_id（owner 隔离键）。"""
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
    )
    return response_base.success(data=data)


@router.get('/decks', summary='演示文稿列表（owner 隔离）', dependencies=[DependsJwtAuth])
async def list_decks(request: Request, db: CurrentSession, limit: int = 20, offset: int = 0) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.list_decks(db, owner_id=owner_id, limit=limit, offset=offset)
    return response_base.success(data=data)


@router.get('/decks/{deck_id}', summary='演示文稿详情', dependencies=[DependsJwtAuth])
async def get_deck(request: Request, db: CurrentSession, deck_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.get_deck(db, owner_id=owner_id, deck_id=deck_id)
    return response_base.success(data=data)


@router.put('/decks/{deck_id}', summary='更新演示文稿', dependencies=[DependsJwtAuth])
async def update_deck(
    request: Request, db: CurrentSessionTransaction, deck_id: int, body: UpdateDeckRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.update_deck(db, owner_id=owner_id, deck_id=deck_id, fields=body.model_dump())
    return response_base.success(data=data)


@router.delete('/decks/{deck_id}', summary='删除演示文稿（软删）', dependencies=[DependsJwtAuth])
async def delete_deck(request: Request, db: CurrentSessionTransaction, deck_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    await deck_service.delete_deck(db, owner_id=owner_id, deck_id=deck_id)
    return response_base.success()


@router.get('/decks/{deck_id}/pages', summary='幻灯片列表', dependencies=[DependsJwtAuth])
async def list_pages(request: Request, db: CurrentSession, deck_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.list_pages(db, owner_id=owner_id, deck_id=deck_id)
    return response_base.success(data=data)


@router.post('/decks/{deck_id}/pages', summary='新增幻灯片', dependencies=[DependsJwtAuth])
async def create_page(
    request: Request, db: CurrentSessionTransaction, deck_id: int, body: CreatePageRequest
) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    data = await deck_service.create_page(
        db,
        owner_id=owner_id,
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
    data = await deck_service.update_page(db, owner_id=owner_id, page_id=page_id, fields=body.model_dump())
    return response_base.success(data=data)


@router.delete('/pages/{page_id}', summary='删除幻灯片（软删）', dependencies=[DependsJwtAuth])
async def delete_page(request: Request, db: CurrentSessionTransaction, page_id: int) -> ResponseModel:
    owner_id = await _resolve_owner(db, request)
    await deck_service.delete_page(db, owner_id=owner_id, page_id=page_id)
    return response_base.success()
