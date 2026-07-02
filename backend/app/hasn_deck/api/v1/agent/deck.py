"""演示文稿 Agent 端 API。

路由前缀: /api/v1/deck/agent
认证方式: Agent JWT（身份取自 JWT claims，绝不读请求体身份）。
访问控制 = 产物级有效权限（应用平台 v3 §6.5）：分身**继承主人权限**，亦可被单独共享（grantee=agent）。
scope 闸：deck:read / deck:write。manager 类操作（改共享名单/可见性）仅 owner 端可做（§8.2：不下放分身）。
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.app.hasn_deck.service.deck_service import Subject, deck_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_capability import require_capability_not_denied
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

_SCOPE_READ = 'deck:read'
_SCOPE_WRITE = 'deck:write'


def _subject(agent: AgentTokenPayload) -> Subject:
    return Subject.agent(agent.agent_hasn_id, agent.owner_hasn_id)


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
    expected_version: int | None = Field(default=None, description='页级乐观锁：分身持有的 rev')


@router.post('/decks', summary='分身创建演示文稿')
async def agent_create_deck(
    db: CurrentSessionTransaction, body: CreateDeckRequest, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_WRITE)
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


@router.get('/style-profiles', summary='可复用样式列表（系统内置 37 风格 ∪ 主人自定义）')
async def agent_list_style_profiles(
    db: CurrentSession, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_READ)
    data = await deck_service.list_style_profiles(db, owner_id=agent.owner_hasn_id)
    return response_base.success(data=data)


@router.get('/decks', summary='分身可访问的演示文稿（主人的 ∪ 共享给分身/主人的 ∪ 企业可见）')
async def agent_list_decks(
    db: CurrentSession, limit: int = 50, offset: int = 0, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_READ)
    data = await deck_service.list_accessible_decks(db, subject=_subject(agent), limit=limit, offset=offset)
    return response_base.success(data=data)


@router.get('/decks/{deck_id}', summary='演示文稿详情')
async def agent_get_deck(
    db: CurrentSession, deck_id: int, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_READ)
    data = await deck_service.get_deck(db, subject=_subject(agent), deck_id=deck_id)
    return response_base.success(data=data)


@router.put('/decks/{deck_id}', summary='更新演示文稿')
async def agent_update_deck(
    db: CurrentSessionTransaction, deck_id: int, body: UpdateDeckRequest, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_WRITE)
    data = await deck_service.update_deck(db, subject=_subject(agent), deck_id=deck_id, fields=body.model_dump())
    return response_base.success(data=data)


@router.delete('/decks/{deck_id}', summary='删除演示文稿（软删，需 manager 权）')
async def agent_delete_deck(
    db: CurrentSessionTransaction, deck_id: int, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_WRITE)
    await deck_service.delete_deck(db, subject=_subject(agent), deck_id=deck_id)
    return response_base.success()


@router.get('/decks/{deck_id}/pages', summary='幻灯片列表')
async def agent_list_pages(
    db: CurrentSession, deck_id: int, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_READ)
    data = await deck_service.list_pages(db, subject=_subject(agent), deck_id=deck_id)
    return response_base.success(data=data)


@router.post('/decks/{deck_id}/pages', summary='新增幻灯片')
async def agent_create_page(
    db: CurrentSessionTransaction, deck_id: int, body: CreatePageRequest, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_WRITE)
    data = await deck_service.create_page(
        db,
        subject=_subject(agent),
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
async def agent_update_page(
    db: CurrentSessionTransaction, page_id: int, body: UpdatePageRequest, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_WRITE)
    fields = body.model_dump(exclude={'expected_version'})
    data = await deck_service.update_page(
        db, subject=_subject(agent), page_id=page_id, fields=fields, expected_version=body.expected_version
    )
    return response_base.success(data=data)


@router.delete('/pages/{page_id}', summary='删除幻灯片（软删）')
async def agent_delete_page(
    db: CurrentSessionTransaction, page_id: int, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    await require_capability_not_denied(db, agent.agent_hasn_id, _SCOPE_WRITE)
    await deck_service.delete_page(db, subject=_subject(agent), page_id=page_id)
    return response_base.success()
