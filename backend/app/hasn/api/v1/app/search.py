"""HASN 用户搜索 API（IM MVP）

按唤星号精确匹配 + 昵称（name）前缀模糊匹配，返回当前用户与该 peer 的现有关系状态，
驱动前端"已是好友 / 已发送 / 可添加"的按钮态。
对应设计文档: docs/hasn-node设计文档/07-产品与端侧/05-消息模块.md §9.2 +
07-联系人模块.md §4 添加联系人入口。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_agents import hasn_agents_dao
from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.hasn.service.hasn_auth import hasn_auth
from backend.app.hasn_im.application.provider import get_relation_gateway
from backend.common.response.response_schema import ResponseModel, response_base
from backend.database.db import CurrentSession

router = APIRouter(prefix='/users', tags=['HASN Users'])


class _RelationReader:
    """保留路由测试接缝，生产读取统一转交 RelationGateway。"""

    @staticmethod
    async def get_relation(
        _db: Any,
        owner_hasn_id: str,
        peer_hasn_id: str,
        _relation_type: str,
    ) -> Any:
        return await get_relation_gateway().resolve_effective_relation(
            owner_hasn_id=owner_hasn_id,
            peer_hasn_id=peer_hasn_id,
        )


hasn_contacts_dao = _RelationReader()


@router.get('/search', summary='按唤星号或昵称搜索用户')
async def search_users(
    db: CurrentSession,
    q: Annotated[str, Query(min_length=2, max_length=64, description='唤星号或昵称前缀')],
    auth: Annotated[dict, Depends(hasn_auth)],
    limit: Annotated[int, Query(ge=1, le=50, description='返回上限')] = 20,
    by: Annotated[str, Query(description='搜索方式 auto|phone（auto=唤星号精确+昵称前缀+手机号精确；phone=仅手机号精确）')] = 'auto',
) -> ResponseModel:
    """搜索用户用于添加好友。

    匹配规则（auto 单输入框统一搜索 唤星号/昵称/手机号）：
    1. 唤星号（star_id）精确命中：包含 `#` 时查 agents，否则查 humans。
    2. 昵称（nickname）前缀模糊匹配：仅 humans，case-insensitive。
    3. 手机号精确命中：仅 humans，精确等值（隐私）。

    返回顺序：精确命中（如有）置顶，其余按 name 字典序。
    自己永远从结果剔除。每条携带 `existing_relation` 字段（pending|connected|archived|blocked|null），
    前端依此显示按钮态。
    """
    self_hasn_id: str = auth.get('effective_id', auth['hasn_id'])

    if by == 'phone':
        # 手机号精确匹配（隐私：精确等值不模糊，仅 humans）。前端「手机号」入口走此分支。
        human = await hasn_humans_dao.search_by_phone(db, q, exclude_hasn_id=self_hasn_id)
        items = [_make_item(human, peer_type='human')] if human else []
    else:
        # auto：唤星号精确命中 + 昵称前缀模糊匹配（原行为）
        items = await _collect_auto_matches(db, q, self_hasn_id=self_hasn_id, limit=limit)

    # 补 existing_relation（驱动前端「已是好友/已发送/可添加」按钮态）
    for item in items:
        relation = await hasn_contacts_dao.get_relation(
            db,
            self_hasn_id,
            item['hasn_id'],
            'social',
        )
        item['existing_relation'] = relation.status if relation else None

    return response_base.success(data={'items': items, 'total': len(items)})


def _make_item(entity: Any, *, peer_type: str) -> dict:
    """把 HasnHumans / HasnAgents 行规范成搜索结果 item。"""
    return {
        'hasn_id': entity.hasn_id,
        'star_id': entity.star_id,
        'name': getattr(entity, 'nickname', None) or getattr(entity, 'display_name', None) or '',
        'avatar': getattr(entity, 'avatar', None) or getattr(entity, 'avatar_url', None),
        'avatar_url': getattr(entity, 'avatar_url', None) or getattr(entity, 'avatar', None),
        'type': peer_type,
        'existing_relation': None,
    }


async def _collect_auto_matches(db: AsyncSession, q: str, *, self_hasn_id: str, limit: int) -> list[dict]:
    """auto 搜索：唤星号精确命中 + 昵称前缀模糊匹配，返回去重后的 items。"""
    items: list[dict] = []
    seen: set[str] = set()

    # 1) 唤星号精确匹配
    if '#' in q:
        agent = await hasn_agents_dao.get_by_star_id(db, q)
        if agent and agent.hasn_id != self_hasn_id:
            items.append(_make_item(agent, peer_type='agent'))
            seen.add(agent.hasn_id)
    else:
        human = await hasn_humans_dao.get_by_star_id(db, q)
        if human and human.hasn_id != self_hasn_id:
            items.append(_make_item(human, peer_type='human'))
            seen.add(human.hasn_id)

    # 2) 昵称前缀模糊匹配（不与精确命中重复）
    if len(items) < limit:
        humans = await hasn_humans_dao.search_by_name(
            db, prefix=q, limit=(limit - len(items)) + 1, exclude_hasn_id=self_hasn_id,
        )
        for h in humans:
            if h.hasn_id in seen:
                continue
            items.append(_make_item(h, peer_type='human'))
            seen.add(h.hasn_id)
            if len(items) >= limit:
                break

    # 3) 手机号精确命中（单输入框统一搜索：唤星号/昵称/手机号一起搜，隐私上仅精确等值）
    if len(items) < limit:
        phone_match = await hasn_humans_dao.search_by_phone(db, q, exclude_hasn_id=self_hasn_id)
        if phone_match and phone_match.hasn_id not in seen:
            items.append(_make_item(phone_match, peer_type='human'))
            seen.add(phone_match.hasn_id)

    return items
