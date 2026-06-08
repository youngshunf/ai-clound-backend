"""演示文稿 Agent 端 API。

路由前缀: /api/v1/deck/agent
认证方式: Agent JWT（身份取自 JWT claims，绝不读请求体身份）。
owner 隔离键 = `agent.owner_hasn_id`（分身代主人操作）；scope 闸：deck:read / deck:write。
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.app.deck.service.deck_service import deck_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth, check_scopes
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

_SCOPE_READ = 'deck:read'
_SCOPE_WRITE = 'deck:write'


class CreateDeckRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255, description='标题')
    topic: str | None = Field(default=None, description='原始主题/brief')
    language: str = Field(default='zh', description='主语言')
    style_profile_id: str | None = Field(default=None, description='引用的 StyleProfile slug')


class UpdateDeckRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    topic: str | None = None
    status: str | None = None
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
    status: str = Field(default='empty')


class UpdatePageRequest(BaseModel):
    position: int | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, max_length=255)
    html: str | None = None
    notes: str | None = None
    layout_intent: str | None = None
    status: str | None = None
    render_state: dict | None = None
    thumb_asset_id: str | None = None


@router.post('/decks', summary='分身创建演示文稿')
async def create_deck(
    db: CurrentSessionTransaction, body: CreateDeckRequest, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await deck_service.create_deck(
        db,
        owner_id=agent.owner_hasn_id,
        title=body.title,
        topic=body.topic,
        language=body.language,
        source='agent',
        style_profile_id=body.style_profile_id,
    )
    return response_base.success(data=data)


@router.get('/decks', summary='分身的（主人）演示文稿列表')
async def list_decks(
    db: CurrentSession, limit: int = 20, offset: int = 0, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_READ])
    data = await deck_service.list_decks(db, owner_id=agent.owner_hasn_id, limit=limit, offset=offset)
    return response_base.success(data=data)


@router.get('/decks/{deck_id}', summary='演示文稿详情')
async def get_deck(
    db: CurrentSession, deck_id: int, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_READ])
    data = await deck_service.get_deck(db, owner_id=agent.owner_hasn_id, deck_id=deck_id)
    return response_base.success(data=data)


@router.put('/decks/{deck_id}', summary='更新演示文稿')
async def update_deck(
    db: CurrentSessionTransaction, deck_id: int, body: UpdateDeckRequest, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await deck_service.update_deck(db, owner_id=agent.owner_hasn_id, deck_id=deck_id, fields=body.model_dump())
    return response_base.success(data=data)


@router.delete('/decks/{deck_id}', summary='删除演示文稿（软删）')
async def delete_deck(
    db: CurrentSessionTransaction, deck_id: int, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    await deck_service.delete_deck(db, owner_id=agent.owner_hasn_id, deck_id=deck_id)
    return response_base.success()


@router.get('/decks/{deck_id}/pages', summary='幻灯片列表')
async def list_pages(
    db: CurrentSession, deck_id: int, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_READ])
    data = await deck_service.list_pages(db, owner_id=agent.owner_hasn_id, deck_id=deck_id)
    return response_base.success(data=data)


@router.post('/decks/{deck_id}/pages', summary='新增幻灯片')
async def create_page(
    db: CurrentSessionTransaction, deck_id: int, body: CreatePageRequest, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await deck_service.create_page(
        db,
        owner_id=agent.owner_hasn_id,
        deck_id=deck_id,
        position=body.position,
        title=body.title,
        html=body.html,
        notes=body.notes,
        layout_intent=body.layout_intent,
        status=body.status,
    )
    return response_base.success(data=data)


@router.put('/pages/{page_id}', summary='更新幻灯片')
async def update_page(
    db: CurrentSessionTransaction, page_id: int, body: UpdatePageRequest, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await deck_service.update_page(db, owner_id=agent.owner_hasn_id, page_id=page_id, fields=body.model_dump())
    return response_base.success(data=data)


@router.delete('/pages/{page_id}', summary='删除幻灯片（软删）')
async def delete_page(
    db: CurrentSessionTransaction, page_id: int, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    await deck_service.delete_page(db, owner_id=agent.owner_hasn_id, page_id=page_id)
    return response_base.success()
